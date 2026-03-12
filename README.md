# BomJesus Railway Deploy

Este projeto foi preparado para rodar na Railway como `worker` ou `cron job`.

## O que foi configurado

- `app.py` roda com Chrome headless quando `HEADLESS=true`
- `Dockerfile` instala Chromium e ChromeDriver no container
- `requirements.txt` fixa as dependencias Python
- `.dockerignore` evita enviar `.env`, `.git` e cache para a imagem

## Variaveis obrigatorias

- `USUARIO`
- `SENHA`
- `SUPABASE_URL`
- `SUPABASE_KEY`

`env.example` e apenas modelo. Nao coloque credenciais reais nele.

## Variaveis opcionais

- `TENANT_ID` default `20`
- `TEXTO_CONFIRMACAO` default `Lead processado via automacao`
- `URL_LOGIN`
- `URL_CLIENTES`
- `LISTA_LEADS_CLASS` default `user-list-item`
- `WAIT_TIMEOUT` default `15`
- `AJAX_WAIT_SECONDS` default `3`
- `HEADLESS` default `true` na Railway
- `NAVIGATION_RETRIES` default `2`
- `PAGE_LOAD_STRATEGY` default `eager`
- `CHROME_EXTRA_ARGS` para flags extras se precisar depurar o Chromium

## Subindo na Railway

1. Crie um projeto na Railway e conecte este repositorio.
2. Deixe a Railway detectar o `Dockerfile`.
3. Configure as variaveis de ambiente usando `.env.example` como referencia.
4. Use o comando padrao do container: `python app.py`.

## Recomendacao de execucao

Como o script termina depois de processar os leads, o modo mais adequado e `cron job`.

Na interface da Railway:

1. Abra o service.
2. Configure a execucao agendada.
3. Defina a frequencia conforme sua operacao.

## Agendamento atual

O projeto ja inclui [railway.json](D:/PUXADA%20PORTAL/BomJesus/railway.json) com:

- `cronSchedule`: `0 */8 * * *`
- `startCommand`: `python app.py`
- `restartPolicyType`: `NEVER`

Isso executa a cada 8 horas em UTC:

- `00:00 UTC`
- `08:00 UTC`
- `16:00 UTC`

No horario de Brasilia (UTC-3), isso normalmente equivale a:

- `21:00`
- `05:00`
- `13:00`

## Teste local

```bash
docker build -t bomjesus .
docker run --rm --env-file .env bomjesus
```
