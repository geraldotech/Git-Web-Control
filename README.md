# Git Web Control

<img width="1190" height="863" alt="image" src="https://github.com/user-attachments/assets/28e11f4d-39a7-4cb1-960c-96d2a4107e64" />


Aplicação Flask para controlar múltiplos repositórios Git da máquina pelo navegador: status, fetch, pull, switch, console Git e botões personalizados por projeto.

## Executar no Windows

Requer Python 3.10+ e Git instalado.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Use **Adicionar projeto** para cadastrar nome, `gitPath` e `repoPath`. Selecione um projeto no painel e use **Configuração** para alterar seu nome ou caminhos. O servidor valida o repositório, salva no `config.json` e aplica a mudança sem reiniciar. Cada projeto possui seus próprios botões personalizados. Também é possível editar o arquivo manualmente:

```json
{
  "projects": [
    {"id": "app", "name": "Aplicação", "gitPath": "git", "repoPath": "D:\\APP", "buttons": []},
    {"id": "site", "name": "Site", "gitPath": "git", "repoPath": "D:\\SITE", "buttons": []}
  ]
}
```

Também pode usar `C:\\Program Files\\Git\\cmd\\git.exe` em `gitPath`. Caminhos relativos em `repoPath` são resolvidos a partir da pasta do arquivo de configuração. Reinicie o servidor apenas quando editar o arquivo manualmente. Se o arquivo estiver ausente ou inválido, o painel continua acessível para corrigir os caminhos. Uma configuração rejeitada não substitui a configuração ativa.

O formato antigo com `gitPath`, `repoPath` e `buttons` na raiz continua aceito como **Meu projeto** e é migrado ao salvar, preservando os botões. Os IDs dos projetos devem ser únicos e estáveis. A seleção fica na URL (`?projectId=...`), permitindo recarregar ou abrir abas com projetos diferentes. Comandos, branches, contagem e botões sempre usam o projeto selecionado.

```powershell
.\.venv\Scripts\python.exe app.py
```

Abra http://localhost:3333 ou `http://<IP-da-máquina-na-VPN>:3333`. O Flask escuta em `0.0.0.0:3333`, sem debug. Restrinja o acesso à rede/VPN de confiança: conforme o escopo, não há login.

A tela mostra a branch atual, branches locais e as saídas stdout/stderr. Para trocar, selecione uma branch ou digite seu nome. Fetch atualiza referências remotas; uma branch remota pode ser selecionada pelo nome digitado usando o comportamento normal do `git switch`. Pull usa a configuração Git existente do repositório. Conflitos e alterações locais são reportados pelo Git, sem resolução automática.

Credenciais e remotos devem estar configurados na máquina para o usuário que executa o servidor. Comandos não solicitam credenciais interativamente e têm limite de 120 segundos. Apenas uma operação é aceita por vez.

## Contagem de commits

Ao abrir a página e após cada comando, o painel executa `git rev-list --count HEAD..@{u}`. O número indica commits presentes no upstream e ausentes no HEAD local. O painel faz fetch ao abrir, trocar de projeto e voltar para a aba (com intervalo mínimo de 30 segundos); **Fetch** força uma nova busca. Durante a operação, os controles ficam desativados para evitar trocar de projeto antes de receber o resultado. Se a branch não tiver upstream, HEAD estiver destacado ou houver outro erro, aparece **Indisponível**, com a mensagem do Git, em vez de um zero incorreto.

## Console e botões personalizados

Digite um comando como `git log --oneline -10` ou `git rev-list --count HEAD..@{u}` no console. O executável usado é sempre o `gitPath` configurado, dentro de `repoPath`. Argumentos com espaços devem ficar entre aspas; barras de caminhos Windows são preservadas. O console aceita subcomandos nativos da instalação do Git, sem aliases, opções globais antes do subcomando, encadeamento (`&&`, `;`), pipes ou redirecionamentos. A execução usa argumentos separados e `shell=False`.

Os comandos mantêm os efeitos normais do Git, incluindo alterações no repositório, configuração e hooks existentes; o console não é um ambiente isolado. Editores e entrada interativa estão desativados, portanto informe mensagens com `-m` em comandos como commit.

Em **Gerenciar botões**, informe um nome e um comando Git e clique em **Adicionar botão**. É possível executar, editar e excluir cada atalho. Até 30 botões por projeto são persistidos na propriedade `buttons` de cada projeto no `config.json`, compartilhados entre acessos ao mesmo projeto e mantidos após reiniciar. Cadastrar um botão apenas salva o comando; a execução acontece ao clicar nele. Botões e console usam a mesma validação.

## API

Para remover o projeto selecionado, abra **Configuração** e clique em **Excluir projeto**. O painel pergunta **Tem certeza?** e exige o resultado correto de uma soma antes de **Confirmar exclusão**. **Cancelar** mantém o projeto. Isso remove apenas o cadastro e seus botões; os arquivos e o histórico Git permanecem no disco. Após excluir, o painel seleciona outro projeto disponível. Também é possível excluir o último projeto e cadastrar um novo na tela vazia.

Endpoints de Git, configuração e botões recebem `?projectId=<id>`. Sem esse parâmetro, usam o primeiro projeto para compatibilidade; IDs desconhecidos retornam 404.

- `GET /api/projects` — lista IDs e nomes
- `POST /api/projects/delete?projectId=<id>` — exclui o cadastro e seus botões; exige um ID explícito e o token CSRF
- `POST /api/projects` com JSON `{"name":"Site","gitPath":"git","repoPath":"D:\\SITE"}` — adiciona um projeto com botões vazios
- `GET /api/git/status`
- `POST /api/git/fetch`
- `POST /api/git/pull`
- `GET /api/git/branches`
- `POST /api/git/switch` com JSON `{"branch":"main"}`
- `GET /api/git/behind` — inclui `count`; retorna `null` quando a contagem não está disponível
- `POST /api/git/console` com JSON `{"command":"git log --oneline -10"}`
- `GET /api/settings` — configuração atual e caminho resolvido do repositório
- `POST /api/settings` com JSON `{"name":"Aplicação","gitPath":"git","repoPath":"D:\\SGA"}` — altera o projeto selecionado
- `POST /api/buttons` com JSON `{"buttons":[{"label":"Log","command":"git log --oneline -10"}]}` — substitui a lista salva

Respostas incluem `success`, `code`, `stdout` e `stderr`. Branches também retorna `branches` e `currentBranch`. As requisições POST exigem o cabeçalho `X-CSRF-Token` com o valor da meta tag `csrf-token` em `/`; a interface envia automaticamente. Isso protege contra chamadas de outras páginas, sem adicionar login. Nomes de branches são validados pelo Git.

## Testar

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

### Git comandos uteis

```bash
git rev-list --count "HEAD..@{u}" # contar commits que estão no upstream mas não localmente
```

Os testes usam repositórios temporários, sem modificar o repositório configurado.

## Gerar execut?vel no Windows

Com o PyInstaller instalado no ambiente, execute na pasta do projeto:

```powershell
pyinstaller --clean --onefile --name appgitweb --add-data "templates;templates" --add-data "static;static" .\app.py
```

Ou use o arquivo de empacotamento que j? inclui templates e arquivos est?ticos:

```powershell
pyinstaller --clean .\appgitweb.spec
```

Execute `dist\appgitweb.exe` e abra http://localhost:3333. O Git precisa estar instalado na m?quina de destino. Para manter os projetos existentes, copie seu `config.json` para a mesma pasta do execut?vel. O painel l? e salva a configura??o nessa pasta, inclusive quando o execut?vel ? iniciado de outro diret?rio. Sem o arquivo, configure o projeto pelo painel. Mantenha o execut?vel em uma pasta em que seu usu?rio possa salvar arquivos.


## compilar executável no Windows

Para compilar o projeto em um executável no Windows, siga os passos abaixo:

1. Instale o PyInstaller no ambiente virtual:

```powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller
```

2. Execute o PyInstaller com os parâmetros necessários:

```powershell
pyinstaller --clean --onefile --name appgitweb .\app.py
```

3. Depois copie templates, static e config.json para dist.

4. Execute o arquivo gerado em `dist\appgitweb.exe` e abra http://localhost:3333 no navegador.
