"""Orquestração das emissões: chama os robôs, repete quando falha e registra tudo.

Este arquivo é o "maestro". Ele não sabe nada sobre nenhum site específico —
só conhece o contrato consultar(cnpj, contexto) -> ResultadoConsulta.

Garantias importantes:
  - Falha em um site NUNCA interrompe a rodada dos outros.
  - Toda tentativa vira uma linha na tabela emissoes, inclusive as que falharam.
  - Toda rodada vira um registro em logs_execucao com o detalhe por certidão.
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select

from app.banco import sessao as abrir_sessao
from app.config import config
from app.modelos import (
    ROTULOS_TIPO,
    Emissao,
    Empresa,
    LogExecucao,
    LogExecucaoItem,
    OrigemEmissao,
    StatusEmissao,
    TipoExecucao,
)
from app.servicos import calcular_situacao, emissoes_por_tipo, listar_pendencias
from robos.base import ResultadoConsulta, pausa_educada
from robos.registro import RoboNaoEncontrado, obter_robo

logger = logging.getLogger(__name__)


# =============================================================================
#  ACOMPANHAMENTO DO PROGRESSO (para a barrinha da tela de Consultas)
# =============================================================================


@dataclass
class ItemProgresso:
    empresa: str
    cnpj: str
    tipo: str
    status: str
    mensagem: str = ""

    @property
    def rotulo_tipo(self) -> str:
        try:
            from app.modelos import TipoCertidao

            return ROTULOS_TIPO[TipoCertidao(self.tipo)]
        except (ValueError, KeyError):
            return self.tipo


@dataclass
class Progresso:
    execucao_id: int | None = None
    total: int = 0
    concluidos: int = 0
    atual: str = ""
    terminado: bool = False
    erro_fatal: str | None = None
    iniciado_em: datetime = field(default_factory=datetime.now)
    itens: list[ItemProgresso] = field(default_factory=list)

    @property
    def porcentagem(self) -> int:
        if self.total <= 0:
            return 100 if self.terminado else 0
        return int(self.concluidos * 100 / self.total)

    def como_dict(self) -> dict:
        return {
            "execucao_id": self.execucao_id,
            "total": self.total,
            "concluidos": self.concluidos,
            "atual": self.atual,
            "terminado": self.terminado,
            "erro_fatal": self.erro_fatal,
            "porcentagem": self.porcentagem,
            "itens": [
                {
                    "empresa": i.empresa,
                    "cnpj": i.cnpj,
                    "tipo": i.rotulo_tipo,
                    "status": i.status,
                    "mensagem": i.mensagem,
                }
                for i in self.itens
            ],
        }


#: Só uma rodada por vez — sites de governo não gostam de consultas simultâneas.
_trava = threading.Lock()
_progresso_atual: Progresso | None = None


def progresso_atual() -> Progresso | None:
    return _progresso_atual


def rodada_em_andamento() -> bool:
    return _progresso_atual is not None and not _progresso_atual.terminado


# =============================================================================
#  UMA CERTIDÃO
# =============================================================================


def montar_contexto(empresa: Empresa, tipo: str, emissao_anterior: Emissao | None) -> dict:
    """Monta o dicionário que é entregue ao robô."""
    return {
        "tipo_certidao": tipo,
        "config": config,
        "regras": config.certidao(tipo),
        "pasta_certidoes": config.pasta_certidoes,
        "pasta_debug": config.pasta_debug,
        "headless": config.navegador.get("headless", True),
        "captcha_disponivel": config.captcha_disponivel,
        "empresa": {
            "id": empresa.id,
            "cnpj": empresa.cnpj,
            "cnpj_matriz": empresa.cnpj_matriz,
            "e_matriz": empresa.e_matriz,
            "razao_social": empresa.razao_social,
            "nome_fantasia": empresa.nome_fantasia,
            "uf": empresa.uf,
            "municipio": empresa.municipio,
            "inscricao_estadual": empresa.inscricao_estadual,
            "inscricao_municipal": empresa.inscricao_municipal,
            "regime_tributario": empresa.regime_tributario,
        },
        "emissao_anterior": (
            {
                "numero_certidao": emissao_anterior.numero_certidao,
                "data_emissao": emissao_anterior.data_emissao,
                "data_validade": emissao_anterior.data_validade,
                "caminho_pdf": emissao_anterior.caminho_pdf,
                "status": emissao_anterior.status,
            }
            if emissao_anterior
            else None
        ),
    }


def consultar_uma(empresa: Empresa, tipo: str, emissao_anterior: Emissao | None = None):
    """Executa um robô com repetição automática.

    Devolve (ResultadoConsulta, tentativas_gastas, duracao_segundos).
    Nunca levanta exceção: qualquer problema vira um ResultadoConsulta de erro.
    """
    execucao_cfg = config.execucao
    maximo = max(1, int(execucao_cfg.get("tentativas_maximas", 3)))
    esperas = list(execucao_cfg.get("espera_entre_tentativas_segundos") or [30, 120, 300])

    cnpj_usado = empresa.cnpj
    if config.certidao(tipo).get("usa_cnpj_matriz") and not empresa.e_matriz:
        cnpj_usado = empresa.cnpj_matriz
        logger.info(
            "%s é filial; a certidão %s será emitida pelo CNPJ da matriz %s.",
            empresa.cnpj,
            tipo,
            cnpj_usado,
        )

    contexto = montar_contexto(empresa, tipo, emissao_anterior)
    inicio = time.monotonic()
    resultado = ResultadoConsulta(
        status=StatusEmissao.ERRO_SITE.value, mensagem_erro="Não executado"
    )

    try:
        robo = obter_robo(tipo)
    except RoboNaoEncontrado as e:
        return (
            ResultadoConsulta(
                status=StatusEmissao.ERRO_SITE.value, mensagem_erro=str(e), permanente=True
            ),
            0,
            time.monotonic() - inicio,
        )

    tentativa = 0
    for tentativa in range(1, maximo + 1):
        try:
            logger.info(
                "[%s] %s — tentativa %d/%d", tipo, empresa.razao_social, tentativa, maximo
            )
            resultado = robo(cnpj_usado, contexto)
        except Exception as e:
            logger.exception("Erro inesperado no robô %s", tipo)
            resultado = ResultadoConsulta(
                status=StatusEmissao.ERRO_SITE.value,
                mensagem_erro=f"Erro inesperado no robô: {e}",
            )
            resultado.detalhes["traceback"] = traceback.format_exc()

        if resultado.sucesso:
            break

        # Erro permanente (robô sem implementação, CNPJ inexistente, certidão
        # desligada): repetir só gastaria tempo.
        if resultado.permanente:
            logger.info("Erro permanente (%s) — não vou repetir.", resultado.mensagem_erro)
            break

        # Captcha sem serviço configurado: repetir não adianta, é limitação conhecida.
        if resultado.status == StatusEmissao.CAPTCHA_FALHOU.value and not config.captcha_disponivel:
            logger.info("Captcha não resolvido e sem serviço configurado — não vou repetir.")
            break

        if tentativa < maximo:
            espera = esperas[min(tentativa - 1, len(esperas) - 1)] if esperas else 30
            logger.info("Falhou (%s). Nova tentativa em %ss.", resultado.status, espera)
            time.sleep(float(espera))

    return resultado, tentativa, time.monotonic() - inicio


def gravar_emissao(
    sessao,
    empresa: Empresa,
    tipo: str,
    resultado: ResultadoConsulta,
    duracao: float,
    origem: str = OrigemEmissao.ROBO.value,
) -> Emissao:
    """Transforma o resultado do robô em uma linha da tabela emissoes."""
    emissao = Emissao(
        empresa_id=empresa.id,
        tipo_certidao=tipo,
        status=resultado.status,
        data_emissao=resultado.data_emissao,
        data_validade=resultado.data_validade,
        numero_certidao=resultado.numero_certidao,
        caminho_pdf=str(resultado.caminho_pdf) if resultado.caminho_pdf else None,
        mensagem_erro=resultado.mensagem_erro,
        html_debug=resultado.html_debug,
        origem=origem,
        duracao_segundos=round(duracao, 2),
    )
    sessao.add(emissao)
    sessao.flush()
    return emissao


# =============================================================================
#  A RODADA INTEIRA
# =============================================================================


def executar_rodada(
    empresa_ids: list[int] | None = None,
    tipos: list[str] | None = None,
    forcar: bool = False,
    tipo_execucao: str = TipoExecucao.MANUAL.value,
    progresso: Progresso | None = None,
) -> int:
    """Emite tudo que está pendente e devolve o id do log de execução.

    Roda de forma síncrona (é chamada dentro de uma thread pelo iniciar_rodada).
    """
    progresso = progresso or Progresso()
    ultimo_site: str | None = None

    with abrir_sessao() as sessao:
        a_emitir, puladas = listar_pendencias(sessao, empresa_ids, tipos, forcar)

        execucao = LogExecucao(
            tipo=tipo_execucao,
            inicio=datetime.now(),
            total_consultas=len(a_emitir),
            total_pulado=len(puladas),
        )
        sessao.add(execucao)
        sessao.flush()
        execucao_id = execucao.id

        progresso.execucao_id = execucao_id
        progresso.total = len(a_emitir)

        for pendencia in puladas:
            sessao.add(
                LogExecucaoItem(
                    execucao_id=execucao_id,
                    empresa_id=pendencia.empresa.id,
                    empresa_nome=pendencia.empresa.razao_social,
                    cnpj=pendencia.empresa.cnpj,
                    tipo_certidao=pendencia.tipo,
                    status="PULADA",
                    mensagem=pendencia.motivo,
                    tentativas=0,
                )
            )
        sessao.commit()

    logger.info(
        "Rodada #%s iniciada: %d certidão(ões) para emitir, %d pulada(s).",
        execucao_id,
        len(a_emitir),
        len(puladas),
    )

    sucessos = falhas = 0

    for pendencia in a_emitir:
        # Cada certidão abre a própria sessão: se uma quebrar, as outras seguem.
        try:
            with abrir_sessao() as sessao:
                empresa = sessao.get(Empresa, pendencia.empresa.id)
                if empresa is None:
                    continue

                tipo = pendencia.tipo
                progresso.atual = f"{empresa.apelido} — {ROTULOS_TIPO.get(tipo, tipo)}"

                # Espera educada entre consultas ao mesmo site.
                if ultimo_site == tipo:
                    pausa_educada(config.execucao)
                ultimo_site = tipo

                anteriores = emissoes_por_tipo(sessao, empresa.id).get(tipo, [])
                anterior_valida = next((e for e in anteriores if e.tem_certidao), None)

                resultado, tentativas, duracao = consultar_uma(empresa, tipo, anterior_valida)
                emissao = gravar_emissao(sessao, empresa, tipo, resultado, duracao)

                if resultado.sucesso:
                    sucessos += 1
                else:
                    falhas += 1

                sessao.add(
                    LogExecucaoItem(
                        execucao_id=execucao_id,
                        empresa_id=empresa.id,
                        empresa_nome=empresa.razao_social,
                        cnpj=empresa.cnpj,
                        tipo_certidao=tipo,
                        status=resultado.status,
                        mensagem=resultado.mensagem_erro
                        or (f"Validade até {emissao.data_validade}" if emissao.data_validade else None),
                        duracao_segundos=round(duracao, 2),
                        tentativas=tentativas,
                    )
                )

                progresso.itens.append(
                    ItemProgresso(
                        empresa=empresa.apelido,
                        cnpj=empresa.cnpj_formatado,
                        tipo=tipo,
                        status=resultado.status,
                        mensagem=resultado.mensagem_erro or "",
                    )
                )
        except Exception:
            # Blindagem final: nada derruba a rodada inteira.
            falhas += 1
            logger.exception(
                "Falha ao processar %s / %s", pendencia.empresa.cnpj, pendencia.tipo
            )
        finally:
            progresso.concluidos += 1

    with abrir_sessao() as sessao:
        execucao = sessao.get(LogExecucao, execucao_id)
        if execucao:
            execucao.fim = datetime.now()
            execucao.total_sucesso = sucessos
            execucao.total_falha = falhas

    progresso.atual = ""
    progresso.terminado = True
    logger.info("Rodada #%s concluída: %d sucesso(s), %d falha(s).", execucao_id, sucessos, falhas)
    return execucao_id


def iniciar_rodada(
    empresa_ids: list[int] | None = None,
    tipos: list[str] | None = None,
    forcar: bool = False,
    tipo_execucao: str = TipoExecucao.MANUAL.value,
) -> Progresso:
    """Dispara a rodada em segundo plano para a tela não travar.

    Se já houver rodada em andamento, devolve o progresso dela em vez de
    começar outra — duas rodadas simultâneas atrapalhariam uma à outra.
    """
    global _progresso_atual

    if not _trava.acquire(blocking=False):
        return _progresso_atual or Progresso(terminado=True, erro_fatal="Rodada em andamento.")

    if _progresso_atual is not None and not _progresso_atual.terminado:
        _trava.release()
        return _progresso_atual

    progresso = Progresso()
    _progresso_atual = progresso

    def _executar() -> None:
        try:
            executar_rodada(empresa_ids, tipos, forcar, tipo_execucao, progresso)
        except Exception as e:
            logger.exception("Erro fatal na rodada")
            progresso.erro_fatal = str(e)
            progresso.terminado = True
        finally:
            progresso.terminado = True
            _trava.release()

    threading.Thread(target=_executar, name="rodada-cnd", daemon=True).start()
    return progresso
