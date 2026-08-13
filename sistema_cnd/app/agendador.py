"""Agendador interno (APScheduler): a rotina que roda sozinha.

Ele NÃO sai emitindo tudo todo dia. A cada rodada ele pergunta ao módulo de
serviços o que está dentro da janela de renovação e emite só isso — o resto
é pulado e registrado como "ainda cedo".

ESTADO: funcional para a rotina diária. Os alertas e o ajuste fino
(limpeza de arquivos antigos, resumo da rodada) entram na Etapa 6.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import config
from app.emissao import iniciar_rodada, rodada_em_andamento
from app.modelos import TipoExecucao

logger = logging.getLogger(__name__)

ID_ROTINA_DIARIA = "rotina_diaria_cnd"

_agendador: BackgroundScheduler | None = None


def _rodada_diaria() -> None:
    """Chamada pelo APScheduler no horário configurado."""
    if rodada_em_andamento():
        logger.warning("Já existe uma rodada em andamento. Pulando a rodada agendada.")
        return
    logger.info("Iniciando a rodada agendada.")
    iniciar_rodada(tipo_execucao=TipoExecucao.AGENDADA.value)


def _fuso():
    """Fuso usado no agendamento.

    Por padrão usa o relógio do próprio computador — é o que a pessoa espera
    quando escreve "06:00" no config.yaml. Só usa outro fuso se ela informar
    um em agendamento.fuso_horario.
    """
    nome = str(config.agendamento.get("fuso_horario") or "").strip()
    if not nome:
        return None
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(nome)
    except Exception:
        logger.warning("Fuso horário '%s' não reconhecido. Usando o relógio do computador.", nome)
        return None


def _gatilho() -> CronTrigger:
    hora_texto = str(config.agendamento.get("hora_execucao", "06:00"))
    try:
        hora, minuto = (int(p) for p in hora_texto.split(":", 1))
    except ValueError:
        logger.warning("hora_execucao inválida (%s). Usando 06:00.", hora_texto)
        hora, minuto = 6, 0

    dias = "mon-fri"
    if config.agendamento.get("incluir_fim_de_semana"):
        dias = "mon-sun"

    fuso = _fuso()
    if fuso is not None:
        return CronTrigger(hour=hora, minute=minuto, day_of_week=dias, timezone=fuso)
    return CronTrigger(hour=hora, minute=minuto, day_of_week=dias)


def iniciar() -> BackgroundScheduler | None:
    """Liga o agendador, se ele estiver ativo no config.yaml."""
    global _agendador

    if not config.agendamento.get("ativo", True):
        logger.info("Agendamento automático desligado no config.yaml.")
        return None

    if _agendador is not None:
        return _agendador

    fuso = _fuso()
    _agendador = BackgroundScheduler(**({"timezone": fuso} if fuso else {}))
    _agendador.add_job(
        _rodada_diaria,
        trigger=_gatilho(),
        id=ID_ROTINA_DIARIA,
        name="Rodada diária de certidões",
        replace_existing=True,
        max_instances=1,
        coalesce=True,       # se o PC estava desligado, roda uma vez só ao voltar
        misfire_grace_time=3600,
    )
    _agendador.start()
    logger.info(
        "Agendador ligado. Próxima rodada: %s", proxima_execucao() or "não programada"
    )
    return _agendador


def parar() -> None:
    global _agendador
    if _agendador is not None:
        _agendador.shutdown(wait=False)
        _agendador = None
        logger.info("Agendador desligado.")


def proxima_execucao():
    """Data/hora da próxima rodada automática, ou None."""
    if _agendador is None:
        return None
    tarefa = _agendador.get_job(ID_ROTINA_DIARIA)
    return tarefa.next_run_time if tarefa else None


def esta_ativo() -> bool:
    return _agendador is not None and _agendador.running
