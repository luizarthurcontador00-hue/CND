# Sistema de Controle e Emissão Automática de CNDs

Programa que roda **na sua máquina** (Windows), emite as certidões negativas de
débito das empresas clientes do escritório e controla a validade de cada uma.

Nada é enviado para a internet além das consultas aos próprios sites oficiais.
Todos os dados ficam em dois lugares seus: o arquivo `banco.db` e a pasta de
certidões.

---

## 1. Instalação (passo a passo, para quem nunca programou)

### Passo 1 — Instalar o Python

1. Acesse **https://www.python.org/downloads/** e clique no botão amarelo grande
   ("Download Python 3.x").
2. Abra o arquivo baixado.
3. **IMPORTANTE:** na primeira tela, marque a caixinha
   **"Add python.exe to PATH"** (fica na parte de baixo da janela).
   Se você não marcar isso, nada vai funcionar.
4. Clique em **"Install Now"** e espere terminar.
5. Reinicie o computador.

### Passo 2 — Colocar a pasta do sistema no lugar

Copie a pasta inteira do sistema para um lugar fixo, por exemplo:

```
C:\SistemaCND\
```

Evite deixar dentro de "Downloads" ou na Área de Trabalho — a pasta vai crescer
com o tempo (os PDFs ficam dentro dela, a menos que você aponte para outro lugar).

### Passo 3 — Primeira execução

Dê **duplo clique** no arquivo:

```
INICIAR.bat
```

Na primeira vez ele vai:

1. preparar o ambiente do Python,
2. baixar as bibliotecas necessárias,
3. baixar o navegador que os robôs usam.

**Isso demora de 3 a 10 minutos e só acontece uma vez.** Nas próximas vezes o
sistema abre em poucos segundos.

Quando terminar, o navegador abre sozinho em **http://localhost:8000**.
Se não abrir, digite esse endereço no navegador manualmente.

### Passo 4 — Uso no dia a dia

- **Para abrir:** duplo clique em `INICIAR.bat`.
- **Para fechar:** feche a janela preta.
- Enquanto a janela preta estiver aberta, o sistema está no ar e o agendamento
  automático funciona. Se a janela estiver fechada, nada roda sozinho.

> **Dica:** para o sistema abrir junto com o Windows, aperte `Windows + R`, digite
> `shell:startup` e arraste um atalho do `INICIAR.bat` para dentro dessa pasta.

---

## 2. As cinco telas

| Tela | Para que serve |
|---|---|
| **Painel** | Tabela empresas × certidões com semáforo de cores. É a tela do dia a dia. |
| **Empresas** | Cadastrar clientes, importar planilha, marcar quais certidões cada um exige. |
| **Consultas** | Botão "consultar agora", individual ou em lote, com barra de progresso. |
| **Histórico** | Todas as emissões, link para abrir o PDF e **upload manual** quando o robô falhar. |
| **Logs** | Cada rodada do robô e os arquivos de depuração das falhas. |

### Como ler o semáforo do Painel

| Cor | Significa |
|---|---|
| 🟢 **Válida** | Em dia, com folga. |
| 🟡 **Vencendo** | Vence em até 15 dias. O robô já vai renovar na próxima rodada. |
| 🔴 **Atenção** | Vencida, ou certidão **positiva** (a empresa tem pendência). Precisa de você. |
| ⬜ **Sem certidão** | Nunca foi consultada, ou o robô falhou na última tentativa. |
| *(vazio)* | Esta empresa não exige esta certidão. |

Clicar na pastilha abre o PDF daquela certidão.

---

## 3. Cadastrar suas empresas

### Uma por uma

Tela **Empresas** → botão **"+ Nova empresa"**.

### Em massa, por planilha

1. Tela **Empresas** → **"Baixar modelo de planilha"**.
2. Abra o `modelo_empresas.csv` no Excel, apague as linhas de exemplo e coloque
   as suas empresas.
3. Salve **mantendo o formato CSV** (ou salve como `.xlsx`, o sistema aceita os dois).
4. Volte na tela Empresas → **"Importar várias empresas de uma vez"** → envie o arquivo.

Colunas da planilha:

| Coluna | Obrigatória | Observação |
|---|---|---|
| `cnpj` | **sim** | Com ou sem pontuação. O sistema confere o dígito verificador. |
| `razao_social` | **sim** | |
| `nome_fantasia` | não | É o nome que aparece no painel. |
| `uf` | não | Sigla de 2 letras. |
| `municipio` | não | |
| `inscricao_estadual` | não | Deixe em branco se a empresa não tiver. |
| `inscricao_municipal` | não | |
| `regime_tributario` | não | Simples Nacional, Lucro Presumido, Lucro Real… |
| `ativa` | não | `sim` ou `nao`. Em branco = ativa. |
| `certidoes` | não | Separadas por vírgula: `FEDERAL,CNDT,FGTS,ESTADUAL_GO,MUNICIPAL`. Em branco = o sistema sugere. |
| `observacoes` | não | |

**Importar de novo o mesmo CNPJ atualiza o cadastro, nunca duplica.**

---

## 4. Quando o robô não consegue emitir

Isso é esperado em duas situações:

**a) Captcha (Federal e FGTS).**
Esses dois sites exigem resolver um captcha difícil (hCaptcha e similar).
Enquanto não houver um serviço pago de resolução configurado, o robô registra
"Captcha não resolvido".
**O sistema não trava por isso.** Emita a certidão à mão no site e anexe o PDF em
**Histórico → "Anexar PDF emitido à mão"**. O sistema lê a validade de dentro do
PDF e o painel volta a ficar verde.

> **O captcha do CNDT (TST) é resolvido sozinho, de graça.** Ele é bem mais
> simples que os outros — 6 letras em posições fixas — e o sistema lê a imagem
> na própria máquina, sem enviar nada para fora e sem serviço pago.
> Melhor ainda: **o sistema aprende com o uso.** Toda vez que uma emissão dá
> certo, ele guarda os caracteres daquele captcha e passa a ler melhor. Nas
> primeiras semanas ele pode precisar de duas ou três tentativas por certidão;
> depois erra cada vez menos.

**b) O site do governo mudou de layout.**
Vá em **Logs → Arquivos de depuração**, baixe o **print (.png)** e o **HTML (.html)**
da falha e me mande. Cada site é um arquivo separado dentro da pasta `robos/`,
então dá para consertar um sem mexer nos outros.

---

## 5. Configuração (`sistema_cnd/config.yaml`)

Abra esse arquivo no **Bloco de Notas**. Ele é todo comentado. O que você mais vai
querer mexer:

| O que | Onde |
|---|---|
| Pasta onde os PDFs são salvos (pode ser pasta de rede) | `armazenamento.pasta_certidoes` |
| Horário da rotina automática | `agendamento.hora_execucao` |
| Ligar/desligar a rotina automática | `agendamento.ativo` |
| Ver o robô trabalhando na tela (para depurar) | `navegador.headless: false` |
| Porta, se a 8000 estiver ocupada | `servidor.porta` |
| Parar de criar empresas de exemplo | `armazenamento.criar_dados_exemplo: false` |

**Regras para não quebrar o arquivo:** não apague os dois-pontos `:`, não mude os
espaços do começo da linha, e escreva caminhos entre aspas.
Feche e abra o programa depois de editar.

---

## 6. Backup

Copie estes dois itens para um pendrive, nuvem ou pasta de rede:

1. **`sistema_cnd/banco.db`** — todo o cadastro e o histórico.
2. **A pasta de certidões** (por padrão `sistema_cnd/certidoes/`) — os PDFs.

Só isso. Nada mais precisa de backup.

---

## 7. Por que o sistema não emite tudo todo dia

Porque isso sobrecarrega os sites do governo e pode gerar **bloqueio do seu IP**.
O robô só emite o que está dentro da **janela de renovação** de cada certidão:

| Certidão | Validade | Só renova a partir de |
|---|---|---|
| Federal (RFB/PGFN) | 180 dias | 30 dias antes de vencer |
| Trabalhista (CNDT) | 180 dias | 30 dias antes de vencer |
| **FGTS (CRF)** | **30 dias** | **10 dias antes de vencer** (regra do próprio site — tentar antes é recusado) |
| Estadual (SEFAZ-GO) | **120 dias** (o PDF emitido diz isso) | 15 dias antes de vencer |

Entre uma consulta e outra no mesmo site o robô espera de 3 a 8 segundos, e faz
no máximo 3 tentativas por certidão.

> A validade é sempre **lida de dentro do PDF emitido**. Os prazos da tabela acima
> são apenas plano B, para quando a leitura do PDF falhar.

---

## 8. Problemas comuns

| Sintoma | O que fazer |
|---|---|
| "O Python não foi encontrado" | Reinstale o Python marcando **"Add python.exe to PATH"**. |
| "A porta 8000 já está em uso" | O sistema provavelmente já está aberto — tente `http://localhost:8000`. Se não for isso, mude `servidor.porta` para 8001. |
| A janela preta abre e fecha na hora | Abra o `INICIAR.bat` e leia a mensagem antes de fechar; ou abra o arquivo `sistema_cnd/logs/sistema.log`. |
| Tudo aparece "Sem certidão" | Normal antes da primeira rodada. Vá em **Consultas → Iniciar consulta**. |
| Uma certidão parou de sair | Tela **Logs**, baixe o `.png` e o `.html` da falha e me mande. |

Para um diagnóstico completo, abra **Sobre / diagnóstico** no rodapé do sistema —
essa tela responde a maior parte das perguntas.

---

## 9. Como o sistema é organizado (referência técnica)

```
sistema_cnd/
├── main.py              inicia o programa
├── config.yaml          todas as configurações (comentado)
├── banco.db             banco de dados (criado sozinho)
├── app/
│   ├── modelos.py       tabelas do banco
│   ├── banco.py         conexão com o SQLite
│   ├── config.py        leitura do config.yaml
│   ├── servicos.py      semáforo e janela de renovação
│   ├── emissao.py       orquestra os robôs, repete quando falha
│   ├── importacao.py    importação de CSV/Excel
│   ├── rotas.py         as telas
│   ├── agendador.py     a rotina automática
│   ├── seed.py          dados de exemplo
│   ├── templates/       HTML das telas
│   └── static/          CSS e JavaScript
├── robos/
│   ├── base.py          contrato + ferramentas comuns a todos os robôs
│   ├── registro.py      liga cada tipo de certidão ao seu módulo
│   ├── federal_rfb.py   Receita Federal / PGFN
│   ├── cndt_tst.py      CNDT / TST
│   ├── ocr_cndt.py      leitor do captcha do TST (funciona offline)
│   ├── captcha.py       serviços pagos de captcha (opcional, desligado)
│   │                    — resolve captcha de imagem e hCaptcha
│   ├── fgts_caixa.py    CRF / Caixa
│   ├── sefaz_go.py      SEFAZ Goiás
│   └── municipal_XXX.py prefeitura a definir
├── dados/               o que o sistema aprende sozinho (modelos de captcha)
├── certidoes/           PDFs: {CNPJ}/{AAAA-MM}/{TIPO}_{AAAAMMDD}.pdf
└── logs/
    ├── sistema.log
    └── debug/           prints e HTML das falhas
```

**A regra de arquitetura mais importante:** cada site é um módulo isolado em
`robos/`, e todos expõem exatamente a mesma função:

```python
def consultar(cnpj: str, contexto: dict) -> ResultadoConsulta
```

Sites de governo mudam de layout com frequência. Essa separação garante que
consertar um site nunca quebre os outros.

---

## 10. Estado atual do desenvolvimento

| Etapa | Situação |
|---|---|
| 1. Estrutura, banco, modelos, interface | ✅ pronta |
| 2. Robô CNDT (TST) | ✅ escrito e testado offline — falta a primeira emissão de verdade na sua máquina |
| 3. Robô SEFAZ-GO | ✅ **pronto e emitindo de verdade** |
| 4. Robô FGTS (Caixa) | 🟡 escrito — depende de rodar na sua máquina (veja abaixo) |
| 5. Robô Federal (RFB/PGFN) | 🟡 escrito — exige hCaptcha (veja abaixo) |
| 6. Agendador e alertas | 🟡 próxima — rotina diária já funciona; faltam os ajustes finos |
| 7. Empacotamento, .bat e README | 🟡 já utilizável; revisão final na última etapa |

### Sobre o robô da Federal (RFB/PGFN)

**Os dois endereços do levantamento saíram do ar (404).** A Receita migrou o
serviço para um portal novo, em `servicos.receitafederal.gov.br/servico/certidoes`,
que é uma aplicação JavaScript moderna — por isso este robô também abre
navegador de verdade.

**O portal usa hCaptcha**, o mesmo tipo da Caixa. Não existe jeito de resolver
isso na própria máquina. Você tem duas saídas, e a primeira é a recomendada:

1. **Emitir à mão e anexar** (sem custo). Emita a certidão no portal e anexe o
   PDF em Histórico → "Anexar PDF emitido à mão". Como a Federal vale 180 dias,
   isso dá umas duas vezes por ano por empresa.
2. **Contratar um serviço de captcha.** Preencha `captcha.provedor` e
   `captcha.chave_api` no `config.yaml` e o robô passa a emitir sozinho.
   Custa poucos centavos por certidão.

**A regra da 2ª via está implementada.** Quando a empresa tem "positiva com
efeitos de negativa", o portal não emite certidão nova. Lendo o próprio código
do portal, encontrei a saída que ele oferece:

> "Emita novas certidões ou consulte certidões emitidas a partir de 22/01/2018
> e emita 2ª via."

Então, quando a emissão nova é recusada, o robô **não devolve erro na hora** —
ele vai à tela de consulta e tenta recuperar a 2ª via de uma certidão anterior
ainda válida. Só desiste se isso também falhar, e nesse caso explica exatamente
o que aconteceu.

**Filiais:** a certidão é emitida pelo CNPJ da matriz e vale para as filiais. O
sistema converte sozinho o CNPJ da filial no da matriz — **recalculando os dois
dígitos verificadores**, que mudam junto. (Aproveitar os dígitos da filial
geraria um CNPJ inválido; foi um erro que o teste pegou antes de virar problema.)

### Sobre o robô do FGTS (leia antes de usar)

O site da Caixa é o mais defendido dos quatro. Antes de qualquer captcha, o
domínio inteiro fica atrás do **ShieldSquare/PerfDrive**, um serviço comercial
anti-robô. Quando ele desconfia do visitante, desvia a navegação e exige um
hCaptcha ("toque no quadro para verificar que você não é robô").

Três consequências:

1. **Este é o único robô que abre navegador de verdade.** A CNDT e a SEFAZ-GO
   usam requisição direta, que é mais leve; aqui isso não funciona, porque o
   ShieldSquare avalia a impressão digital do navegador.

2. **O bloqueio depende muito da sua conexão.** Endereços de servidor/nuvem são
   barrados quase sempre; a internet comum de um escritório costuma passar sem
   nem ver o desafio. Por isso o comportamento na sua máquina tende a ser bem
   melhor do que em qualquer teste feito fora dela.

3. **Se o desafio aparecer, você resolve UMA vez.** Coloque
   `navegador.headless: false` no `config.yaml`, rode a consulta do FGTS e
   resolva o "não sou um robô" na janela que abrir. A sessão liberada fica
   guardada em `dados/sessao_fgts.json` e é reaproveitada nas próximas
   emissões — não precisa repetir a cada empresa.

Se nada disso funcionar na sua rede, o sistema registra "Captcha não resolvido"
com a explicação, e você emite o CRF à mão e anexa pela tela de Histórico. O
controle de validade continua correto.

**O que ainda preciso de você:** não consegui abrir o formulário do CRF durante
o desenvolvimento (o endereço de rede usado era barrado), então não vi os nomes
reais dos campos. Para não inventar, o robô **procura** os elementos pelo que
eles são — um campo que fale em CNPJ, um botão escrito "Consultar". Isso deve
funcionar, mas se falhar, os arquivos de depuração da tela **Logs** me mostram
a página real e eu ajusto rápido.

### Sobre o robô da SEFAZ-GO

Este é o primeiro robô **validado emitindo certidões de verdade**: o teste
automatizado emite dois documentos reais no site da SEFAZ e confere o resultado.

Três coisas saíram diferentes do levantamento inicial:

1. **O endereço mudou.** O `001frmEmiteCertidao_c.asp` redireciona para
   `default.asp`. O robô já aponta para o endereço final.
2. **Existe uma tela de confirmação** que não constava no levantamento, e ela
   só aparece às vezes: quando o CNPJ tem cadastro em Goiás, o site pergunta
   "Confirma o Nome do Contribuinte: FULANO?" antes de emitir. Quando não tem,
   o PDF vem direto. O robô trata os dois caminhos.
3. **A validade é de 120 dias, não ~60.** O PDF diz, em texto,
   "Certidao VALIDA POR 120 DIAS", e não imprime nenhuma data em número — a
   data de emissão aparece só por extenso ("GOIANIA, 13 AGOSTO DE 2026").
   O sistema lê as duas coisas e calcula a validade. Foi exatamente por isso
   que ficou combinado não fixar prazo no código.

Confirmado também que **a SEFAZ-GO não usa captcha**, então o robô emite
sozinho, sem nenhuma intervenção sua.

### Sobre o robô do CNDT

O levantamento inicial dizia que o site do TST pedia "só CNPJ + verificação de
segurança". Na prática essa verificação **é um captcha**. Foi preciso resolvê-lo
para a certidão sair — e isso está feito, sem custo nenhum.

O robô não abre navegador: o site é um formulário antigo e todo o fluxo cabe em
quatro requisições HTTP. Isso o deixa mais rápido, mais estável e **muito mais
leve para o servidor do TST** (um navegador dispararia mais de dez requisições
por emissão, o que atrai bloqueio de IP).

O leitor de captcha foi medido com validação cruzada: **92% de acerto por
caractere** em imagens que ele nunca tinha visto. Como o robô tenta até 4
captchas diferentes por certidão, isso dá cerca de **97% de chance de emitir**.
Quando não consegue, registra "Captcha não resolvido" e você anexa o PDF à mão —
o controle de validade continua correto de qualquer jeito.

Enquanto um robô não estiver pronto, ele registra "ainda não implementado" no
histórico e **não atrapalha os demais** — é exatamente assim que o sistema se
comporta quando um site cai de verdade.
