"""Ligação entre um tipo de certidão e o módulo que sabe emiti-la.

Para acrescentar um site novo:
    1. crie robos/meu_site.py com a função consultar(cnpj, contexto),
    2. acrescente a linha no dicionário abaixo,
    3. acrescente o tipo em app/modelos.py (TipoCertidao) e no config.yaml.

Nenhum outro arquivo precisa ser alterado.
"""

from __future__ import annotations

import importlib
import logging
from typing import Callable

from robos.base import ResultadoConsulta

logger = logging.getLogger(__name__)

#: tipo de certidão -> nome do módulo dentro do pacote robos
MODULOS: dict[str, str] = {
    "FEDERAL": "robos.federal_rfb",
    "CNDT": "robos.cndt_tst",
    "FGTS": "robos.fgts_caixa",
    "ESTADUAL_GO": "robos.sefaz_go",
    "MUNICIPAL": "robos.municipal_prodata",
}


class RoboNaoEncontrado(Exception):
    pass


def obter_robo(tipo_certidao: str) -> Callable[[str, dict], ResultadoConsulta]:
    """Devolve a função consultar() do robô responsável por esse tipo."""
    nome_modulo = MODULOS.get(tipo_certidao)
    if not nome_modulo:
        raise RoboNaoEncontrado(
            f"Não existe robô cadastrado para a certidão '{tipo_certidao}'."
        )

    try:
        modulo = importlib.import_module(nome_modulo)
    except ImportError as e:
        raise RoboNaoEncontrado(f"Não consegui carregar o módulo {nome_modulo}: {e}") from e

    funcao = getattr(modulo, "consultar", None)
    if not callable(funcao):
        raise RoboNaoEncontrado(
            f"O módulo {nome_modulo} não tem a função consultar(cnpj, contexto)."
        )
    return funcao


def tipos_disponiveis() -> list[str]:
    return list(MODULOS)
