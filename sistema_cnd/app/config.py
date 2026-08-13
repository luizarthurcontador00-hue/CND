"""Leitura do config.yaml.

Todo o resto do sistema pega configuração por aqui, nunca lendo o YAML direto.
Assim, se o formato do arquivo mudar, só este arquivo precisa ser ajustado.
"""

from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Any

import yaml

# Pasta sistema_cnd/ (duas pastas acima deste arquivo: app/config.py -> app -> sistema_cnd)
RAIZ_PROJETO = Path(__file__).resolve().parent.parent

ARQUIVO_PADRAO = RAIZ_PROJETO / "config.yaml"
# Se existir um config.local.yaml, ele tem prioridade. Serve para você guardar
# chaves de API sem que elas entrem no controle de versão.
ARQUIVO_LOCAL = RAIZ_PROJETO / "config.local.yaml"

logger = logging.getLogger(__name__)

# Valores usados quando a chave não existe no YAML. Garante que o sistema sobe
# mesmo com um config.yaml incompleto ou editado errado.
PADROES: dict[str, Any] = {
    "servidor": {"host": "127.0.0.1", "porta": 8000, "abrir_navegador_ao_iniciar": True},
    "armazenamento": {
        "pasta_certidoes": "certidoes",
        "pasta_logs": "logs",
        "banco_dados": "banco.db",
        "pasta_dados": "dados",
        "criar_dados_exemplo": True,
    },
    "navegador": {
        "headless": True,
        "timeout_ms": 60000,
        "camera_lenta_ms": 0,
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
    },
    "captcha": {"provedor": "nenhum", "chave_api": "", "timeout_segundos": 180},
    "agendamento": {
        "ativo": True,
        "hora_execucao": "06:00",
        "incluir_fim_de_semana": False,
        "fuso_horario": "",
        "dias_antes_para_renovar": 30,
        "dias_alerta_amarelo": 15,
    },
    "execucao": {
        "intervalo_minimo_segundos": 3,
        "intervalo_maximo_segundos": 8,
        "tentativas_maximas": 3,
        "espera_entre_tentativas_segundos": [30, 120, 300],
        "paralelismo": 1,
    },
    "certidoes": {},
    "log": {"nivel": "INFO", "salvar_debug_em_erro": True, "apagar_debug_apos_dias": 30},
}


def _mesclar(base: dict, novo: dict) -> dict:
    """Mescla o YAML lido por cima dos padrões, sem perder chaves ausentes."""
    resultado = copy.deepcopy(base)
    for chave, valor in (novo or {}).items():
        if isinstance(valor, dict) and isinstance(resultado.get(chave), dict):
            resultado[chave] = _mesclar(resultado[chave], valor)
        else:
            resultado[chave] = valor
    return resultado


class Config:
    """Acesso à configuração, com caminhos já resolvidos para caminhos absolutos."""

    def __init__(self, dados: dict[str, Any], arquivo: Path):
        self._dados = dados
        self.arquivo = arquivo

    # ------------------------------------------------------------------ acesso
    def obter(self, *caminho: str, padrao: Any = None) -> Any:
        """Lê um valor aninhado: config.obter('navegador', 'headless')."""
        atual: Any = self._dados
        for parte in caminho:
            if not isinstance(atual, dict) or parte not in atual:
                return padrao
            atual = atual[parte]
        return atual

    @property
    def dados(self) -> dict[str, Any]:
        return self._dados

    # ------------------------------------------------------------------ seções
    @property
    def servidor(self) -> dict:
        return self._dados["servidor"]

    @property
    def navegador(self) -> dict:
        return self._dados["navegador"]

    @property
    def captcha(self) -> dict:
        return self._dados["captcha"]

    @property
    def agendamento(self) -> dict:
        return self._dados["agendamento"]

    @property
    def execucao(self) -> dict:
        return self._dados["execucao"]

    @property
    def log(self) -> dict:
        return self._dados["log"]

    def certidao(self, tipo: str) -> dict:
        """Regras de uma certidão específica (FEDERAL, CNDT, FGTS...)."""
        return self._dados.get("certidoes", {}).get(tipo, {}) or {}

    # ----------------------------------------------------------------- caminhos
    def _resolver(self, valor: str) -> Path:
        caminho = Path(str(valor).strip())
        if not caminho.is_absolute():
            caminho = RAIZ_PROJETO / caminho
        return caminho

    @property
    def pasta_certidoes(self) -> Path:
        return self._resolver(self._dados["armazenamento"]["pasta_certidoes"])

    @property
    def pasta_logs(self) -> Path:
        return self._resolver(self._dados["armazenamento"]["pasta_logs"])

    @property
    def pasta_debug(self) -> Path:
        return self.pasta_logs / "debug"

    @property
    def pasta_dados(self) -> Path:
        """Onde o sistema guarda o que aprende sozinho (modelos de captcha)."""
        return self._resolver(self._dados["armazenamento"].get("pasta_dados", "dados"))

    @property
    def arquivo_modelos_captcha(self) -> Path:
        return self.pasta_dados / "modelos_captcha_cndt.json"

    @property
    def arquivo_banco(self) -> Path:
        return self._resolver(self._dados["armazenamento"]["banco_dados"])

    def criar_pastas(self) -> None:
        for pasta in (
            self.pasta_certidoes,
            self.pasta_logs,
            self.pasta_debug,
            self.pasta_dados,
        ):
            pasta.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ captcha
    @property
    def captcha_disponivel(self) -> bool:
        """True só quando há provedor E chave configurados."""
        provedor = str(self.captcha.get("provedor", "nenhum")).lower().strip()
        chave = str(self.captcha.get("chave_api", "")).strip()
        return provedor not in ("", "nenhum") and bool(chave)


def carregar(arquivo: Path | None = None) -> Config:
    """Carrega o config.yaml (ou config.local.yaml, se existir)."""
    if arquivo is None:
        arquivo = ARQUIVO_LOCAL if ARQUIVO_LOCAL.exists() else ARQUIVO_PADRAO

    dados_yaml: dict = {}
    if arquivo.exists():
        try:
            with open(arquivo, "r", encoding="utf-8") as f:
                dados_yaml = yaml.safe_load(f) or {}
        except yaml.YAMLError as erro:
            # Não derruba o sistema: avisa e sobe com os padrões, para o usuário
            # conseguir abrir a tela e entender que errou a edição do arquivo.
            logger.error(
                "Erro de formatação no %s (%s). Usando as configurações padrão. "
                "Verifique espaços e dois-pontos no arquivo.",
                arquivo.name,
                erro,
            )
    else:
        logger.warning("Arquivo %s não encontrado. Usando configurações padrão.", arquivo)

    return Config(_mesclar(PADROES, dados_yaml), arquivo)


# Instância única usada por todo o sistema.
config = carregar()
