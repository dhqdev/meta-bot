"""Tito, o TI: cuida da conexão com o MetaTrader 5, dos dados e da manutenção."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select

from app.agents.base import Agent, AgentProfile
from app.agents.skills import SkillDef
from app.broker.mt5 import MT5Error, MT5Unavailable
from app.db import session_scope
from app.events import bus
from app.kv import kv_get, kv_set
from app.models import Activity, AgentMessage, EquitySnapshot, SkillEvent
from app.runtime import get_config


class InfraAgent(Agent):
    profile = AgentProfile(
        id="infra",
        name="Tito",
        role="TI e Dados",
        emoji="🔧",
        uses_ai=False,
        description="Mantém o MetaTrader 5 conectado (qualquer corretora), sincroniza os candles, estima o fuso do servidor e faz a manutenção do banco.",
    )
    interval = 10.0
    idle_task = "Monitorando o servidor"
    always_on = True
    skill_defs = [
        SkillDef("conexao_mt5", "Conexão MT5", "Mantém o terminal conectado e logado na corretora."),
        SkillDef("dados", "Sincronização de dados", "Mantém os candles dos ativos atualizados em cache."),
        SkillDef("manutencao", "Manutenção", "Limpa registros antigos e mantém o banco leve."),
    ]

    def __init__(self, office):
        super().__init__(office)
        self.status: dict = {"configured": False, "connected": False, "checked_at": None}
        self._healthy_streak = 0
        self._announced_down = False

    async def tick(self) -> None:
        if self.due("health", 20):
            await self.check_mt5()
        if self.is_running() and self.due("warm", 300):
            await self.warm_cache()
        if self.due("housekeeping", 3600):
            self.housekeeping()

    async def on_system_change(self, running: bool) -> None:
        if running:
            self.set_state("idle", "desk", "Monitorando o servidor", "🖥️")
        else:
            self.set_state("idle", "server", "De plantão no servidor (sistema desligado)", "🛠️")

    # ------------------------------------------------------------- MT5
    async def check_mt5(self) -> None:
        office = self.office
        term = office.terminals.active()
        client = office.terminals.client()
        now = datetime.now(timezone.utc).isoformat()
        if client is None:
            self.status = {"configured": False, "connected": False, "checked_at": now, "message": "Nenhum terminal MT5 configurado: usando o mercado simulado."}
            office.market.mt5_ok = False
            self._publish()
            return
        self.work("Checando o terminal MT5", "server", "🔌")
        started = time.perf_counter()
        try:
            health = await client.health()
        except (MT5Error, MT5Unavailable) as exc:
            self._down(exc.message, now)
            return
        latency = round((time.perf_counter() - started) * 1000)
        if not health.get("initialized"):
            self._down(health.get("error") or "terminal ainda abrindo", now)
            return
        account = health.get("account") or {}
        # login automático com a conta cadastrada na tela
        if term and term.get("login") and term.get("password") and term.get("server") and str(account.get("login")) != str(term["login"]):
            try:
                self.work(f"Entrando na conta {term['login']} ({term['server']})", "server", "🔑")
                res = await client.login(int(term["login"]), term["password"], term["server"])
                account = res.get("account") or account
                self.log(f"Login feito na corretora: conta {term['login']} em {term['server']}", kind="mt5")
                self.skills.gain("conexao_mt5", 10, "login na corretora")
            except (MT5Error, MT5Unavailable, ValueError) as exc:
                self.log(f"Login na corretora falhou: {getattr(exc, 'message', exc)}", kind="mt5", level="warning")
        connected = bool(health.get("connected")) and bool(account)
        self._estimate_offset(health.get("server_time_hint"))
        office.market.mt5_ok = connected
        self.status = {
            "configured": True,
            "connected": connected,
            "trade_allowed": bool(health.get("trade_allowed")),
            "checked_at": now,
            "latency_ms": latency,
            "terminal": term.get("name") if term else "",
            "login": account.get("login"),
            "server": account.get("server"),
            "company": account.get("company"),
            "currency": account.get("currency"),
            "balance": account.get("balance"),
            "offset_hours": office.market.offset_hours,
            "message": "Conectado" if connected else "Terminal aberto, mas sem conta conectada (faça login pela tela do MT5 ou em Configurações).",
        }
        if connected:
            self._healthy_streak += 1
            if self._announced_down:
                self.tell("all", "🔌 " + self.line("mt5_up", server=str((self.status or {}).get("server") or "corretora")), kind="info")
                self.log("Conexão com o MetaTrader 5 restabelecida", kind="mt5")
                self.skills.gain("conexao_mt5", 10, "reconexão")
                self._announced_down = False
            if self._healthy_streak % 30 == 1:
                self.skills.gain("conexao_mt5", 2, "terminal saudável")
        self.idle("Monitorando o servidor" if connected else "Esperando o login na corretora")
        self._publish()

    def _down(self, message: str, now: str) -> None:
        self.office.market.mt5_ok = False
        self._healthy_streak = 0
        self.status = {"configured": True, "connected": False, "checked_at": now, "message": message}
        if not self._announced_down:
            self.tell("all", "⚠️ " + self.line("mt5_down") + f" ({message[:60]})", kind="alerta")
            self.log(f"MetaTrader 5 indisponível: {message}. Usando o mercado simulado enquanto isso.", kind="mt5", level="warning")
            self._announced_down = True
        self.set_state("alert", "server", f"MT5 indisponível: {message[:80]}", "⚠️")
        self._publish()

    def _estimate_offset(self, hint: dict | None) -> None:
        """Fuso do servidor da corretora = hora do último tick - hora UTC (quando o mercado está aberto)."""
        if not hint:
            saved = kv_get("mt5_offset_hours")
            if saved is not None:
                self.office.market.auto_offset_hours = float(saved)
            return
        diff = int(hint.get("tick_time", 0)) - int(hint.get("utc_now", 0))
        half_hours = round(diff / 1800)
        residual = abs(diff - half_hours * 1800)
        if residual <= 180 and abs(half_hours) <= 28:
            offset = half_hours / 2
            if offset != self.office.market.auto_offset_hours:
                self.office.market.auto_offset_hours = offset
                self.office.market.clear_cache()
                kv_set("mt5_offset_hours", offset)
                self.log(f"Fuso do servidor da corretora: UTC{offset:+g}", kind="mt5")

    def _publish(self) -> None:
        bus.publish({"type": "mt5", **self.status, "source": self.office.market.source()})

    # ----------------------------------------------------------- dados
    async def warm_cache(self) -> None:
        cfg = get_config()
        self.work("Atualizando os candles dos ativos", "server", "💾")
        ok = 0
        for symbol in cfg.watchlist:
            for tf in cfg.timeframes:
                try:
                    await self.office.market.rates(symbol, tf, 500)
                    ok += 1
                except Exception:
                    continue
        if ok:
            self.skills.gain("dados", 1, f"{ok} séries atualizadas")
        self.idle("Monitorando o servidor")

    # ----------------------------------------------------- manutenção
    def housekeeping(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=45)
        with session_scope() as s:
            removed = s.execute(delete(Activity).where(Activity.ts < cutoff)).rowcount or 0
            total = s.scalar(select(func.count(Activity.id))) or 0
            if total > 60000:
                keep_from = s.scalar(select(Activity.id).order_by(Activity.id.desc()).offset(50000).limit(1))
                if keep_from:
                    removed += s.execute(delete(Activity).where(Activity.id < keep_from)).rowcount or 0
            s.execute(delete(SkillEvent).where(SkillEvent.ts < datetime.now(timezone.utc) - timedelta(days=180), SkillEvent.kind == "xp"))
            s.execute(delete(EquitySnapshot).where(EquitySnapshot.ts < datetime.now(timezone.utc) - timedelta(days=400)))
            s.execute(delete(AgentMessage).where(AgentMessage.ts < datetime.now(timezone.utc) - timedelta(days=60)))
        if removed:
            self.skills.gain("manutencao", 2, f"{removed} registros antigos removidos")
