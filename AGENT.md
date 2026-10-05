# Git Web Control — MVP

## Objetivo

Criar uma pequena aplicação web para executar comandos Git básicos remotamente através do navegador, acessando a máquina pela VPN já existente.

A aplicação deve usar o Git instalado na própria máquina onde o projeto/repositório está localizado.

## Escopo inicial

Criar uma interface simples com poucos recursos.

Comandos suportados:

- `git status`
- `git fetch`
- `git pull`
- `git switch`

Para `git switch`, permitir:

1. Listar branches existentes.
2. Selecionar uma branch.
3. Informar manualmente o nome da branch.
4. Executar:

```bash
git switch nome-da-branch
```

## Configuração

A aplicação deve ter configuração semelhante a:

```json
{
  "gitPath": "C:\\Program Files\\Git\\cmd\\git.exe",
  "repoPath": "D:\\SGA"
}
```

Se o Git estiver disponível no `PATH`, pode usar apenas:

```text
git
```

Também pode detectar automaticamente:

```python
import shutil

git_path = shutil.which("git")
```

## Backend

Sugestão:

- Python
- Flask
- `subprocess.run`
- Interface HTML simples

Exemplo de execução:

```python
subprocess.run(
    ["git", "status"],
    cwd=repo_path,
    capture_output=True,
    text=True
)
```

Ou usando caminho completo:

```python
subprocess.run(
    [git_path, "pull"],
    cwd=repo_path,
    capture_output=True,
    text=True
)
```

## Endpoints

Criar inicialmente:

```http
GET /api/git/status
POST /api/git/fetch
POST /api/git/pull
GET /api/git/branches
POST /api/git/switch
```

Exemplo para trocar de branch:

```json
POST /api/git/switch

{
  "branch": "main"
}
```

Resposta padrão:

```json
{
  "success": true,
  "code": 0,
  "stdout": "",
  "stderr": ""
}
```

## Interface

Página única simples.

Exemplo:

```text
Git Web Control

Projeto:
D:\SGA

Branch atual:
main

[ STATUS ]
[ FETCH ]
[ PULL ]

Branches:
[ main ▼ ]

[ SWITCH ]

Resultado:

Already up to date.
```

## Listagem de branches

Pode usar:

```bash
git branch --format=%(refname:short)
```

Para descobrir a branch atual:

```bash
git branch --show-current
```

## Segurança

Não permitir receber comandos arbitrários pelo navegador.

Não implementar algo como:

```text
/api/exec?cmd=...
```

Não usar:

```python
subprocess.run(
    command,
    shell=True
)
```

com conteúdo vindo diretamente do usuário.

Os comandos devem ser fixos e controlados pela aplicação.

Exemplo:

```python
COMMANDS = {
    "status": ["git", "status"],
    "fetch": ["git", "fetch"],
    "pull": ["git", "pull"]
}
```

Para `git switch`, validar o nome da branch antes de executar.

## Requisitos do MVP

A aplicação deve:

- identificar se o Git está instalado;
- validar se `repoPath` é um repositório Git;
- mostrar branch atual;
- listar branches;
- executar `status`;
- executar `fetch`;
- executar `pull`;
- executar `switch`;
- exibir stdout e stderr na interface;
- funcionar inicialmente em Windows;
- rodar localmente via Flask.

Exemplo:

```bash
python app.py
```

Disponível em:

```text
http://0.0.0.0:5050
```

## Fora do escopo neste momento

Não implementar ainda:

- login;
- múltiplos projetos;
- deploy;
- Docker;
- restart de serviços;
- histórico;
- WebSocket;
- comandos arbitrários;
- push;
- merge;
- reset;
- checkout de commit;
- gerenciamento de credenciais.

O objetivo agora é somente validar que uma aplicação web consegue controlar o Git instalado na máquina usando os comandos básicos definidos acima.