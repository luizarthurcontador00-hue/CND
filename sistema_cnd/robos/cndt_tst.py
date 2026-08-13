"""Certidão Negativa de Débitos Trabalhistas — CNDT / TST.

Site: https://cndt-certidao.tst.jus.br/gerarCertidao.faces
Validade: 180 dias (mas lida do PDF sempre que possível).

ESTADO: esqueleto — implementação completa na Etapa 2.
"""

from __future__ import annotations

import logging

from robos.base import ResultadoConsulta, erro

logger = logging.getLogger(__name__)

URL_EMISSAO = "https://cndt-certidao.tst.jus.br/gerarCertidao.faces"
TIPO = "CNDT"


def consultar(cnpj: str, contexto: dict) -> ResultadoConsulta:
    """Emite a CNDT do CNPJ informado."""
    return erro(
        "Robô da CNDT ainda não implementado (previsto para a Etapa 2).", permanente=True
    )
