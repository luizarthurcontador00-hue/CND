"""Certificado de Regularidade do FGTS — CRF / Caixa Econômica Federal.

Site: https://consulta-crf.caixa.gov.br/consultacrf/pages/consultaEmpregador.jsf

Observações que valem para a implementação (Etapa 4):
  - CNPJ completo, SÓ NÚMEROS, e a UF fica EM BRANCO
    (a UF só é usada quando se consulta pelo CNPJ básico, de 8 dígitos).
  - Tem captcha.
  - Resultado é "Regular" ou "Irregular". Se Regular, há botão para imprimir
    o certificado.
  - Validade: 30 dias, e só pode ser renovado A PARTIR DO 10º DIA ANTERIOR ao
    vencimento — antes disso o site recusa. Quem respeita essa janela é o
    agendador (config.yaml -> certidoes.FGTS.janela_renovacao_dias).

ESTADO: esqueleto — implementação completa na Etapa 4.
"""

from __future__ import annotations

import logging

from robos.base import ResultadoConsulta, erro

logger = logging.getLogger(__name__)

URL_CONSULTA = "https://consulta-crf.caixa.gov.br/consultacrf/pages/consultaEmpregador.jsf"
TIPO = "FGTS"


def consultar(cnpj: str, contexto: dict) -> ResultadoConsulta:
    """Emite o CRF/FGTS do CNPJ informado."""
    return erro(
        "Robô do FGTS ainda não implementado (previsto para a Etapa 4).", permanente=True
    )
