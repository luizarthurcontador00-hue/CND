"""Ponto de partida do Sistema de CNDs.

Para rodar:
    python main.py

Depois abra http://localhost:8000 no navegador (o programa tenta abrir sozinho).
Para parar: feche a janela preta ou aperte Ctrl+C.
"""

from __future__ import annotations

import logging
import sys
import threading
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

# Permite rodar com "python main.py" de dentro da pasta sistema_cnd/.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from app import agendador  # noqa: E402
from app.banco import criar_tabelas  # noqa: E402
from app.config import RAIZ_PROJETO, config  # noqa: E402
from app.rotas import pagina_erro, rotas  # noqa: E402
from app.seed import criar_dados_exemplo  # noqa: E402

logger = logging.getLogger("sistema_cnd")


def configurar_log() -> None:
    """Escreve as mensagens na tela e no arquivo logs/sistema.log."""
    config.criar_pastas()
    nivel = getattr(logging, str(config.log.get("nivel", "INFO")).upper(), logging.INFO)

    formato = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s", datefmt="%d/%m/%Y %H:%M:%S"
    )

    raiz = logging.getLogger()
    raiz.setLevel(nivel)
    raiz.handlers.clear()

    na_tela = logging.StreamHandler(sys.stdout)
    na_tela.setFormatter(formato)
    raiz.addHandler(na_tela)

    try:
        no_arquivo = logging.FileHandler(
            config.pasta_logs / "sistema.log", encoding="utf-8"
        )
        no_arquivo.setFormatter(formato)
        raiz.addHandler(no_arquivo)
    except OSError as e:
        print(f"Aviso: não consegui gravar o arquivo de log ({e}). Seguindo só com a tela.")

    # Silencia o barulho de rotina do servidor web.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


@asynccontextmanager
async def ciclo_de_vida(_app: FastAPI):
    """Roda uma vez ao ligar e uma vez ao desligar o programa."""
    criar_tabelas()

    criadas = criar_dados_exemplo()
    if criadas:
        logger.info(
            "Criadas %d empresas de exemplo para você conhecer o sistema. "
            "Apague-as pela tela de Empresas quando cadastrar as suas.",
            criadas,
        )

    agendador.iniciar()

    endereco = f"http://localhost:{config.servidor.get('porta', 8000)}"
    print()
    print("=" * 66)
    print("  SISTEMA DE CNDs NO AR")
    print(f"  Abra no navegador:  {endereco}")
    print("  Para parar: feche esta janela ou aperte Ctrl+C")
    print("=" * 66)
    print()

    if config.servidor.get("abrir_navegador_ao_iniciar", True):
        threading.Timer(1.5, lambda: webbrowser.open(endereco)).start()

    yield

    agendador.parar()
    logger.info("Sistema encerrado.")


def criar_app() -> FastAPI:
    app = FastAPI(
        title="Sistema de CNDs",
        description="Controle e emissão automática de certidões negativas de débito.",
        version="0.1.0",
        lifespan=ciclo_de_vida,
        docs_url="/api/docs",
        redoc_url=None,
    )
    app.mount(
        "/static",
        StaticFiles(directory=str(RAIZ_PROJETO / "app" / "static")),
        name="static",
    )
    app.include_router(rotas)

    # ------------------------------------------------------ páginas de erro
    from fastapi.exceptions import RequestValidationError  # noqa: PLC0415
    from starlette.exceptions import HTTPException as ErroHTTP  # noqa: PLC0415

    @app.exception_handler(404)
    async def _nao_encontrado(request, _erro):
        return pagina_erro(
            request,
            404,
            "Página não encontrada",
            "O endereço digitado não existe neste sistema. "
            "Use o menu no topo para navegar.",
            "🔍",
        )

    @app.exception_handler(RequestValidationError)
    async def _dados_invalidos(request, _erro):
        return pagina_erro(
            request,
            400,
            "Endereço ou dados inválidos",
            "Algum valor enviado não está no formato esperado. "
            "Volte e tente de novo pela tela, sem digitar o endereço à mão.",
            "✋",
        )

    @app.exception_handler(ErroHTTP)
    async def _erro_http(request, erro: ErroHTTP):
        if erro.status_code == 404:
            return await _nao_encontrado(request, erro)
        return pagina_erro(
            request, erro.status_code, "Não foi possível concluir", str(erro.detail)
        )

    @app.exception_handler(Exception)
    async def _erro_inesperado(request, erro: Exception):
        logger.exception("Erro inesperado ao atender %s", request.url.path)
        return pagina_erro(
            request,
            500,
            "Erro inesperado no sistema",
            f"Detalhe técnico: {erro}. O erro completo foi gravado em "
            f"{config.pasta_logs / 'sistema.log'} — me mande esse arquivo.",
            "💥",
        )

    return app


app = criar_app()


def porta_ocupada(host: str, porta: int) -> bool:
    """Verifica se já há alguma coisa usando a porta."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1" if host == "0.0.0.0" else host, porta)) == 0


def main() -> None:
    import uvicorn

    configurar_log()

    host = config.servidor.get("host", "127.0.0.1")
    porta = int(config.servidor.get("porta", 8000))

    if porta_ocupada(host, porta):
        print()
        print("=" * 66)
        print(f"  A PORTA {porta} JA ESTA EM USO")
        print()
        print("  Quase sempre isso quer dizer que o sistema JA ESTA ABERTO.")
        print(f"  Tente abrir no navegador:  http://localhost:{porta}")
        print()
        print("  Se não for isso, feche as outras janelas pretas do sistema,")
        print("  ou mude a porta no config.yaml (servidor.porta) para 8001.")
        print("=" * 66)
        input("\nAperte Enter para fechar…")
        return

    uvicorn.run(app, host=host, port=porta, log_level="warning")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nSistema encerrado pelo usuário.")
    except Exception as erro:  # pragma: no cover
        print()
        print("=" * 66)
        print("  O SISTEMA NÃO CONSEGUIU INICIAR")
        print(f"  Motivo: {erro}")
        print()
        print("  Verificações rápidas:")
        print("   1. As dependências foram instaladas?")
        print("      pip install -r requirements.txt")
        print("   2. A porta 8000 está livre? Se não, mude em config.yaml.")
        print("   3. O config.yaml foi editado e ficou com erro de espaçamento?")
        print("=" * 66)
        input("\nAperte Enter para fechar…")
        raise
