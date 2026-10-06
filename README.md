# Git Web Controlx

Aplicação Flask para controlar um repositório Git da máquina pelo navegador: status, fetch, pull, switch, console Git e botões personalizados.

## Executar no Windows

Requer Python 3.10+ e Git instalado.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Configure `gitPath` e `repoPath` diretamente no painel e clique em **Salvar configuração**. O servidor valida o repositório, salva no `config.json` e aplica a mudança sem reiniciar. Também é possível editar o arquivo manualmente:

```json
{
  "gitPath": "git",
  "repoPath": "D:\\APP"
}
```

Também pode usar `C:\\Program Files\\Git\\cmd\\git.exe` em `gitPath`. Caminhos relativos em `repoPath` são resolvidos a partir da pasta do arquivo de configuração. Reinicie o servidor apenas quando editar o arquivo manualmente. Se o arquivo estiver ausente ou inválido, o painel continua acessível para corrigir os caminhos. Uma configuração rejeitada não substitui a configuração ativa.

```powershell
.\.venv\Scripts\python.exe app.py
```

Abra http://localhost:3333 ou `http://<IP-da-máquina-na-VPN>:3333`. O Flask escuta em `0.0.0.0:3333`, sem debug. Restrinja o acesso à rede/VPN de confiança: conforme o escopo, não há login.

A tela mostra a branch atual, branches locais e as saídas stdout/stderr. Para trocar, selecione uma branch ou digite seu nome. Fetch atualiza referências remotas; uma branch remota pode ser selecionada pelo nome digitado usando o comportamento normal do `git switch`. Pull usa a configuração Git existente do repositório. Conflitos e alterações locais são reportados pelo Git, sem resolução automática.

Credenciais e remotos devem estar configurados na máquina para o usuário que executa o servidor. Comandos não solicitam credenciais interativamente e têm limite de 120 segundos. Apenas uma operação é aceita por vez.

## Contagem de commits

Ao abrir a página e após cada comando, o painel executa `git rev-list --count HEAD..@{u}`. O número indica commits presentes no upstream e ausentes no HEAD local. Não faz fetch automaticamente: clique em **Fetch** para atualizar as referências remotas. Se a branch não tiver upstream, HEAD estiver destacado ou houver outro erro, aparece **Indisponível**, com a mensagem do Git, em vez de um zero incorreto.

## Console e botões personalizados

Digite um comando como `git log --oneline -10` ou `git rev-list --count HEAD..@{u}` no console. O executável usado é sempre o `gitPath` configurado, dentro de `repoPath`. Argumentos com espaços devem ficar entre aspas; barras de caminhos Windows são preservadas. O console aceita subcomandos nativos da instalação do Git, sem aliases, opções globais antes do subcomando, encadeamento (`&&`, `;`), pipes ou redirecionamentos. A execução usa argumentos separados e `shell=False`.

Os comandos mantêm os efeitos normais do Git, incluindo alterações no repositório, configuração e hooks existentes; o console não é um ambiente isolado. Editores e entrada interativa estão desativados, portanto informe mensagens com `-m` em comandos como commit.

Em **Botões personalizados**, informe um nome e um comando Git e clique em **Adicionar botão**. É possível executar, editar e excluir cada atalho. Até 30 botões são persistidos na propriedade `buttons` do `config.json`, compartilhados entre acessos ao painel e mantidos após reiniciar. Cadastrar um botão apenas salva o comando; a execução acontece ao clicar nele. Botões e console usam a mesma validação.

## API

- `GET /api/git/status`
- `POST /api/git/fetch`
- `POST /api/git/pull`
- `GET /api/git/branches`
- `POST /api/git/switch` com JSON `{"branch":"main"}`
- `GET /api/git/behind` — inclui `count`; retorna `null` quando a contagem não está disponível
- `POST /api/git/console` com JSON `{"command":"git log --oneline -10"}`
- `GET /api/settings` — configuração atual e caminho resolvido do repositório
- `POST /api/settings` com JSON `{"gitPath":"git","repoPath":"D:\\SGA"}`
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
