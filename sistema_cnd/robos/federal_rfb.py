"""Certidão Conjunta Federal — Receita Federal / PGFN (inclui INSS).

Observações que valem para a implementação (Etapa 5):
  - Emitida pelo CNPJ da MATRIZ e vale para as filiais.
  - Validade: 180 dias (lida do PDF quando possível).
  - Tem captcha (hCaptcha). Sem serviço de resolução configurado, o robô
    devolve CAPTCHA_FALHOU e o usuário emite à mão pela tela de Histórico.
    O sistema NUNCA trava por causa disso.
  - REGRA ESPECIAL: quando a empresa tem "positiva com efeitos de negativa",
    o portal NÃO emite uma nova certidão. Nesse caso o robô precisa tentar
    recuperar a 2ª VIA de uma certidão anterior ainda válida
    (contexto["emissao_anterior"]) antes de devolver erro.

ESTADO: esqueleto — implementação completa na Etapa 5.
"""

from __future__ import annotations

import logging

from robos.base import ResultadoConsulta, erro

logger = logging.getLogger(__name__)

TIPO = "FEDERAL"


def consultar(cnpj: str, contexto: dict) -> ResultadoConsulta:
    """Emite a certidão conjunta federal do CNPJ da matriz."""
    return erro(
        "Robô da Receita Federal ainda não implementado (previsto para a Etapa 5).",
        permanente=True,
    )
