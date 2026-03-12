import os
import json
import re
import shlex
import shutil
import subprocess
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from selenium import webdriver
from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from supabase import Client, create_client

load_dotenv()

DEFAULT_URL_LOGIN = "https://sistema.autocerto.com/auth/Login"
DEFAULT_URL_CLIENTES = "https://sistema.autocerto.com/LeadV2/Lista"
DEFAULT_TENANT_ID = "20"
DEFAULT_TEXTO_CONFIRMACAO = "Lead processado via automacao"
DEFAULT_LISTA_LEADS_CLASS = "user-list-item"
DEFAULT_WAIT_TIMEOUT = 15
DEFAULT_AJAX_WAIT_SECONDS = 3.0
DEFAULT_NAVIGATION_RETRIES = 2
DEFAULT_PAGE_LOAD_STRATEGY = "eager"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/146.0.0.0 Safari/537.36"
)
DEFAULT_EVOLUTION_REPORT_DELAY = 0

URL_LOGIN = os.getenv("URL_LOGIN", DEFAULT_URL_LOGIN)
URL_CLIENTES = os.getenv("URL_CLIENTES", DEFAULT_URL_CLIENTES)
USUARIO = os.getenv("USUARIO")
SENHA = os.getenv("SENHA")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
TENANT_ID = os.getenv("TENANT_ID", DEFAULT_TENANT_ID)
TEXTO_CONFIRMACAO = os.getenv("TEXTO_CONFIRMACAO", DEFAULT_TEXTO_CONFIRMACAO)
LISTA_LEADS_CLASS = os.getenv("LISTA_LEADS_CLASS", DEFAULT_LISTA_LEADS_CLASS)
WAIT_TIMEOUT = int(os.getenv("WAIT_TIMEOUT", str(DEFAULT_WAIT_TIMEOUT)))
AJAX_WAIT_SECONDS = float(os.getenv("AJAX_WAIT_SECONDS", str(DEFAULT_AJAX_WAIT_SECONDS)))
NAVIGATION_RETRIES = int(os.getenv("NAVIGATION_RETRIES", str(DEFAULT_NAVIGATION_RETRIES)))
PAGE_LOAD_STRATEGY = os.getenv("PAGE_LOAD_STRATEGY", DEFAULT_PAGE_LOAD_STRATEGY).lower()
CHROME_EXTRA_ARGS = shlex.split(os.getenv("CHROME_EXTRA_ARGS", ""))
USER_AGENT = os.getenv("USER_AGENT", DEFAULT_USER_AGENT)
EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY")
EVOLUTION_INSTANCE = os.getenv("EVOLUTION_INSTANCE")
EVOLUTION_REPORT_TO = os.getenv("EVOLUTION_REPORT_TO")
EVOLUTION_REPORT_DELAY = int(os.getenv("EVOLUTION_REPORT_DELAY", str(DEFAULT_EVOLUTION_REPORT_DELAY)))


def ler_env_bool(nome_variavel, padrao=False):
    valor = os.getenv(nome_variavel)
    if valor is None:
        return padrao

    return valor.strip().lower() in {"1", "true", "t", "yes", "y", "on"}


HEADLESS = ler_env_bool(
    "HEADLESS",
    padrao=bool(os.getenv("RAILWAY_ENVIRONMENT_NAME") or os.getenv("RAILWAY_ENVIRONMENT_ID")),
)
EVOLUTION_REPORT_ENABLED = ler_env_bool(
    "EVOLUTION_REPORT_ENABLED",
    padrao=bool(EVOLUTION_API_URL and EVOLUTION_API_KEY and EVOLUTION_INSTANCE),
)


def utc_now():
    return datetime.now(timezone.utc)


def formatar_timestamp(timestamp):
    if not timestamp:
        return "-"

    return timestamp.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def normalizar_destinatario_evolution(valor):
    if not valor:
        return None

    if "@" in valor:
        valor = valor.split("@", 1)[0]

    numero = re.sub(r"\D", "", valor)
    return numero or None


def obter_destinatarios_relatorio_evolution():
    if not EVOLUTION_REPORT_TO:
        return []

    candidatos = re.split(r"[,;\n]+", EVOLUTION_REPORT_TO)
    destinatarios = []

    for candidato in candidatos:
        numero = normalizar_destinatario_evolution(candidato)
        if not numero or numero in destinatarios:
            continue

        destinatarios.append(numero)
        if len(destinatarios) == 3:
            break

    return destinatarios


def montar_url(base_url, path):
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def fazer_requisicao_json(url, method="GET", headers=None, payload=None, timeout=30):
    body = None
    request_headers = dict(headers or {})

    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")

    request = Request(url=url, data=body, headers=request_headers, method=method)

    try:
        with urlopen(request, timeout=timeout) as response:
            response_body = response.read().decode("utf-8", errors="replace")
            if not response_body:
                return None

            return json.loads(response_body)
    except HTTPError as exc:
        detalhes = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} em {url}: {detalhes}") from exc
    except URLError as exc:
        raise RuntimeError(f"Falha de rede em {url}: {exc.reason}") from exc


def obter_headers_evolution():
    return {
        "apikey": EVOLUTION_API_KEY,
        "Accept": "application/json",
    }


def enviar_relatorio_evolution(texto_relatorio):
    if not EVOLUTION_REPORT_ENABLED:
        print("Relatorio Evolution desabilitado.")
        return False

    if not EVOLUTION_API_URL or not EVOLUTION_API_KEY or not EVOLUTION_INSTANCE:
        print("Relatorio Evolution ignorado: configuracao incompleta.")
        return False

    destinatarios = obter_destinatarios_relatorio_evolution()
    if not destinatarios:
        print("Relatorio Evolution ignorado: defina de 1 a 3 numeros em EVOLUTION_REPORT_TO.")
        return False

    enviados = 0
    url = montar_url(EVOLUTION_API_URL, f"/message/sendText/{EVOLUTION_INSTANCE}")
    for destinatario in destinatarios:
        payload = {
            "number": destinatario,
            "text": texto_relatorio,
            "delay": EVOLUTION_REPORT_DELAY,
            "linkPreview": False,
        }
        resposta = fazer_requisicao_json(url, method="POST", headers=obter_headers_evolution(), payload=payload)
        status = None
        if isinstance(resposta, dict):
            status = resposta.get("status")

        print(f"Relatorio enviado via Evolution API para {destinatario}. status={status or 'desconhecido'}")
        enviados += 1

    return enviados > 0


def montar_relatorio_execucao(resumo_execucao):
    ambiente = os.getenv("RAILWAY_ENVIRONMENT_NAME") or "local"
    service_name = os.getenv("RAILWAY_SERVICE_NAME") or "BomJesus"
    status_texto = {
        "success": "SUCESSO",
        "no_data": "SEM DADOS",
        "error": "ERRO",
    }.get(resumo_execucao.get("status"), str(resumo_execucao.get("status", "DESCONHECIDO")).upper())

    linhas = [
        f"Relatorio BomJesus [{service_name}]",
        f"Status: {status_texto}",
        f"Ambiente: {ambiente}",
        f"Tenant ID: {resumo_execucao.get('tenant_id') or '-'}",
        f"Inicio: {formatar_timestamp(resumo_execucao.get('inicio_utc'))}",
        f"Fim: {formatar_timestamp(resumo_execucao.get('fim_utc'))}",
        f"Duracao: {resumo_execucao.get('duracao_segundos', 0):.1f}s",
        f"Leads detectados: {resumo_execucao.get('leads_detectados', 0)}",
        f"Leads processados: {resumo_execucao.get('leads_processados', 0)}",
        f"Leads ignorados: {resumo_execucao.get('leads_ignorados', 0)}",
        f"Leads repetidos: {resumo_execucao.get('leads_repetidos', 0)}",
        f"Erros por lead: {resumo_execucao.get('erros_lead', 0)}",
        f"Confirmacoes enviadas: {resumo_execucao.get('confirmacoes_enviadas', 0)}",
        f"Registros unicos: {resumo_execucao.get('registros_unicos', 0)}",
        f"Supabase afetados: {resumo_execucao.get('supabase_registros', 0)}",
    ]

    erro = resumo_execucao.get("erro")
    if erro:
        linhas.append(f"Erro: {erro}")

    preview = resumo_execucao.get("preview_processados") or []
    if preview:
        linhas.append("Preview:")
        for item in preview[:5]:
            linhas.append(f"- {item}")

    return "\n".join(linhas)


def validar_configuracao():
    faltantes = []

    for nome, valor in {
        "USUARIO": USUARIO,
        "SENHA": SENHA,
        "SUPABASE_URL": SUPABASE_URL,
        "SUPABASE_KEY": SUPABASE_KEY,
    }.items():
        if not valor:
            faltantes.append(nome)

    if faltantes:
        faltantes_texto = ", ".join(faltantes)
        raise RuntimeError(f"Variaveis obrigatorias ausentes: {faltantes_texto}")


def encontrar_binario_chrome():
    candidatos = [
        os.getenv("CHROME_BIN"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("google-chrome"),
        shutil.which("chrome"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]

    for candidato in candidatos:
        if candidato and os.path.exists(candidato):
            return candidato

    return None


def obter_versao_binario(caminho_binario):
    if not caminho_binario:
        return None

    try:
        resultado = subprocess.run(
            [caminho_binario, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception:
        return None

    saida = (resultado.stdout or resultado.stderr or "").strip()
    return saida or None


def formatar_erro(exc):
    partes = [type(exc).__name__]
    mensagem = getattr(exc, "msg", None) or str(exc).strip()
    if mensagem:
        partes.append(mensagem)

    stacktrace = getattr(exc, "stacktrace", None)
    if stacktrace:
        resumo = " | ".join(str(linha).strip() for linha in stacktrace[:3])
        if resumo:
            partes.append(f"stacktrace={resumo}")

    return " | ".join(partes)


def abrir_url(driver, url, tentativas=NAVIGATION_RETRIES, contexto="pagina"):
    ultimo_erro = None

    for tentativa in range(1, tentativas + 1):
        try:
            print(f"Acessando {contexto}: {url} (tentativa {tentativa}/{tentativas})")
            driver.get(url)
            print(f"{contexto.capitalize()} carregada: url_atual={driver.current_url!r} titulo={driver.title!r}")
            return
        except Exception as exc:
            ultimo_erro = exc
            print(f"Falha ao abrir {contexto}: {formatar_erro(exc)}")
            time.sleep(2)

    raise RuntimeError(f"Nao foi possivel abrir {contexto}: {formatar_erro(ultimo_erro)}") from ultimo_erro


def verificar_bloqueio_acesso(driver, contexto):
    titulo = (driver.title or "").strip()
    html = (driver.page_source or "")[:4000]
    html_normalizado = html.lower()

    indicadores = [
        "access denied",
        "forbidden",
        "request blocked",
        "cloudflare",
        "attention required",
        "incapsula",
        "akamai",
        "perimeterx",
    ]

    if titulo.lower() in {"access denied", "forbidden"} or any(indicador in html_normalizado for indicador in indicadores):
        trecho = re.sub(r"\s+", " ", html).strip()[:500]
        raise RuntimeError(
            f"Acesso bloqueado ao abrir {contexto}. "
            f"titulo={titulo!r} trecho_html={trecho!r}"
        )


def criar_driver_chrome():
    print(f"Iniciando o navegador (headless={'sim' if HEADLESS else 'nao'})...")

    options = webdriver.ChromeOptions()
    options.page_load_strategy = PAGE_LOAD_STRATEGY
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    chrome_args = [
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-setuid-sandbox",
        "--disable-software-rasterizer",
        "--disable-extensions",
        "--disable-background-networking",
        "--disable-background-timer-throttling",
        "--disable-blink-features=AutomationControlled",
        "--disable-renderer-backgrounding",
        "--disable-features=Translate,AcceptCHFrame,MediaRouter,OptimizationHints",
        "--no-sandbox",
        "--no-zygote",
        "--window-size=1365,768",
        "--lang=pt-BR",
        f"--user-agent={USER_AGENT}",
        "--user-data-dir=/tmp/chrome-user-data",
        "--data-path=/tmp/chrome-data",
        "--disk-cache-dir=/tmp/chrome-cache",
    ]

    if HEADLESS:
        chrome_args.append("--headless")

    chrome_args.extend(CHROME_EXTRA_ARGS)
    for arg in chrome_args:
        options.add_argument(arg)

    chrome_bin = encontrar_binario_chrome()
    if chrome_bin:
        options.binary_location = chrome_bin

    chromedriver_path = os.getenv("CHROMEDRIVER_PATH") or shutil.which("chromedriver")
    print(f"Chrome binario: {chrome_bin or 'nao encontrado'}")
    print(f"Chrome versao: {obter_versao_binario(chrome_bin) or 'desconhecida'}")
    print(f"ChromeDriver: {chromedriver_path or 'nao encontrado'}")
    print(f"ChromeDriver versao: {obter_versao_binario(chromedriver_path) or 'desconhecida'}")
    print(f"Page load strategy: {PAGE_LOAD_STRATEGY}")
    if CHROME_EXTRA_ARGS:
        print(f"Args extras do Chrome: {CHROME_EXTRA_ARGS}")

    service = ChromeService(executable_path=chromedriver_path) if chromedriver_path else ChromeService()

    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(60)
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {
            "source": """
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
                Object.defineProperty(navigator, 'language', {get: () => 'pt-BR'});
                Object.defineProperty(navigator, 'languages', {get: () => ['pt-BR', 'pt', 'en-US', 'en']});
            """
        },
    )
    capabilities = driver.capabilities or {}
    print(
        "Sessao Chrome iniciada: "
        f"browserVersion={capabilities.get('browserVersion')!r} "
        f"platformName={capabilities.get('platformName')!r}"
    )
    print(f"User-Agent configurado: {USER_AGENT}")
    return driver


def fazer_login(url, usuario, senha):
    driver = criar_driver_chrome()
    wait = WebDriverWait(driver, WAIT_TIMEOUT)

    abrir_url(driver, url, contexto="a pagina de login")
    verificar_bloqueio_acesso(driver, "a pagina de login")

    wait.until(EC.visibility_of_element_located((By.ID, "login")))
    print("Preenchendo o formulario de login...")
    driver.find_element(By.ID, "login").send_keys(usuario)
    driver.find_element(By.ID, "senha").send_keys(senha)
    driver.find_element(By.TAG_NAME, "button").click()

    gerenciador_leads_xpath = "//a[contains(@class, 'loadPageAjax') and .//span[contains(text(), 'Gerenciador')]]"
    try:
        wait.until(EC.element_to_be_clickable((By.XPATH, gerenciador_leads_xpath)))
        print("Login realizado com sucesso.")
    except TimeoutException:
        print("Aviso: o login demorou ou a pagina inicial mudou. Tentando prosseguir...")

    return driver


def obter_tenant_id(_driver):
    return TENANT_ID


def processar_e_coletar_leads(driver, url, tenant_id, texto_confirmacao):
    wait = WebDriverWait(driver, WAIT_TIMEOUT)
    dados_coletados = []
    assinaturas_tentadas = set()
    identificadores_tentados = set()
    lead_numero = 0
    resumo = {
        "leads_detectados": 0,
        "leads_processados": 0,
        "leads_ignorados": 0,
        "leads_repetidos": 0,
        "erros_lead": 0,
        "confirmacoes_enviadas": 0,
    }

    print("\n--- Buscando lista de Leads ---")

    def carregar_lista_leads():
        abrir_url(driver, url, contexto="a lista de leads")
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(AJAX_WAIT_SECONDS)
        return driver.find_elements(By.CLASS_NAME, LISTA_LEADS_CLASS)

    def obter_assinatura_lead(elemento):
        partes = []

        for atributo in ("data-id", "data-lead-id", "id", "href", "onclick"):
            try:
                valor = elemento.get_attribute(atributo)
            except StaleElementReferenceException:
                valor = None

            if valor:
                partes.append(f"{atributo}={valor}")

        try:
            texto_base = elemento.text or elemento.get_attribute("innerText") or ""
        except StaleElementReferenceException:
            texto_base = ""

        texto_base = re.sub(r"\s+", " ", texto_base).strip()
        if texto_base:
            partes.append(f"texto={texto_base[:160]}")

        if not partes:
            try:
                html = elemento.get_attribute("outerHTML") or ""
            except StaleElementReferenceException:
                html = ""

            html = re.sub(r"\s+", " ", html).strip()
            if html:
                partes.append(f"html={html[:160]}")

        return " | ".join(partes)

    def buscar_texto_por_label(soup, label_text):
        label = soup.find(lambda tag: tag.name in ["span", "label", "strong"] and label_text in tag.text)
        if label:
            proximo = label.find_next_sibling()
            if proximo:
                return proximo.get_text(strip=True)

            parent = label.parent
            return parent.get_text(strip=True).replace(label_text, "").strip()

        return None

    try:
        elementos_leads = carregar_lista_leads()
        num_total_leads = len(elementos_leads)
        resumo["leads_detectados"] = num_total_leads

        if num_total_leads == 0:
            print(f"Nenhum lead encontrado com a classe '{LISTA_LEADS_CLASS}'.")
            return [], resumo

        print(f"Contagem detectada: {num_total_leads} leads para processar.")
    except Exception as exc:
        print(f"Erro ao carregar a lista inicial: {exc}")
        return [], resumo

    while True:
        assinatura_atual = None
        numero_execucao = lead_numero + 1

        try:
            leads_na_lista = carregar_lista_leads()
            if not leads_na_lista:
                print("Nenhum lead restante na lista.")
                break

            lead_atual = None
            for lead in leads_na_lista:
                assinatura_candidata = obter_assinatura_lead(lead)
                if not assinatura_candidata or assinatura_candidata in assinaturas_tentadas:
                    continue

                lead_atual = lead
                assinatura_atual = assinatura_candidata
                break

            if lead_atual is None:
                print("Nenhum lead novo encontrado na lista atual.")
                break

            lead_numero += 1
            numero_execucao = lead_numero
            print(f"\n--- Lead {numero_execucao}/{num_total_leads} ---")

            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", lead_atual)
            time.sleep(1)

            try:
                lead_atual.click()
            except Exception:
                driver.execute_script("arguments[0].click();", lead_atual)

            print(" -> Extraindo dados...")
            wait.until(EC.presence_of_element_located((By.XPATH, '//span[contains(text(), "Telefone")]')))
            soup_detalhes = BeautifulSoup(driver.page_source, "html.parser")

            telefone_bruto = buscar_texto_por_label(soup_detalhes, "Telefone")
            telefone = re.sub(r"\D", "", telefone_bruto) if telefone_bruto else ""

            nome_tag = soup_detalhes.find(class_="lead__text--name")
            nome = nome_tag.get_text(strip=True) if nome_tag else None

            email = buscar_texto_por_label(soup_detalhes, "Email")
            cpf = buscar_texto_por_label(soup_detalhes, "CPF")
            identificador_lead = telefone or cpf or email or nome or assinatura_atual

            if identificador_lead in identificadores_tentados:
                print(" -> [IGNORADO] Lead repetido detectado apos recarregar a lista.")
                assinaturas_tentadas.add(assinatura_atual)
                resumo["leads_repetidos"] += 1
                continue

            veiculo = "Nao informado"
            try:
                veiculo_element = driver.find_element(By.XPATH, '(//p[b])[1]/b')
                veiculo = veiculo_element.text.strip()
            except Exception:
                pass

            if not telefone or len(telefone) < 8 or not nome:
                print(" -> [IGNORADO] Dados insuficientes. O sistema nao enviara confirmacao nem salvara.")
                assinaturas_tentadas.add(assinatura_atual)
                identificadores_tentados.add(identificador_lead)
                resumo["leads_ignorados"] += 1
                continue

            try:
                print(" -> Tentando enviar confirmacao no sistema...")
                btn_comentario = wait.until(EC.element_to_be_clickable((By.ID, "btnComentario")))
                btn_comentario.click()

                campo_texto = wait.until(EC.visibility_of_element_located((By.ID, "comentario")))
                campo_texto.clear()
                campo_texto.send_keys(texto_confirmacao)

                btn_salvar = wait.until(EC.element_to_be_clickable((By.ID, "btnSalvarComentarioInterno")))
                btn_salvar.click()

                print(f" -> Sucesso! Mensagem '{texto_confirmacao}' enviada.")
                resumo["confirmacoes_enviadas"] += 1
                time.sleep(1.5)
            except TimeoutException:
                print(" -> [ERRO DE ENVIO] Nao foi possivel encontrar os botoes de comentario.")
            except Exception as exc:
                print(f" -> [ERRO DE ENVIO] Falha ao tentar comentar: {exc}")

            soup_atualizado = BeautifulSoup(driver.page_source, "html.parser")

            msg_recebida, msg_enviada = None, None
            try:
                msgs_cliente = soup_atualizado.find_all(class_=re.compile("interacaoCliente"))
                if msgs_cliente:
                    msg_recebida = msgs_cliente[-1].get_text(strip=True)

                msgs_loja = soup_atualizado.find_all(class_=re.compile("interacaoLoja"))
                if msgs_loja:
                    msg_enviada = msgs_loja[-1].get_text(strip=True)
            except Exception:
                pass

            dados_coletados.append(
                {
                    "tenantid": tenant_id,
                    "nome": nome or "Desconhecido",
                    "email": email or "",
                    "telefone": telefone,
                    "cpf": cpf or "",
                    "veiculo": veiculo,
                    "mensagem_recebida": msg_recebida,
                    "mensagem_enviada": msg_enviada,
                }
            )

            assinaturas_tentadas.add(assinatura_atual)
            identificadores_tentados.add(identificador_lead)
            resumo["leads_processados"] += 1
            print(" -> Lead finalizado e salvo na lista temporaria.")
        except Exception as exc:
            if assinatura_atual:
                assinaturas_tentadas.add(assinatura_atual)
            resumo["erros_lead"] += 1
            print(f"Erro no lead {numero_execucao}: {exc}")
            continue

    print("\n--- Processo concluido. ---")
    return dados_coletados, resumo


def salvar_no_supabase(url_db, key_db, lista_clientes):
    if not lista_clientes:
        print("Nenhum cliente valido para salvar.")
        return 0

    try:
        supabase: Client = create_client(url_db, key_db)
        response = supabase.table("leads_portais").upsert(
            lista_clientes,
            on_conflict="telefone,tenantid",
        ).execute()

        dados = getattr(response, "data", None)
        if isinstance(dados, list):
            count = len(dados)
        elif dados:
            count = 1
        else:
            count = 0

        print(f"Sucesso. Registros afetados no Supabase: {count}")
        return count
    except Exception as exc:
        print(f"Erro critico ao salvar no Supabase: {exc}")
        raise


def main():
    validar_configuracao()

    driver = None
    inicio_utc = utc_now()
    resumo_execucao = {
        "status": "success",
        "erro": None,
        "tenant_id": TENANT_ID,
        "inicio_utc": inicio_utc,
        "fim_utc": None,
        "duracao_segundos": 0.0,
        "leads_detectados": 0,
        "leads_processados": 0,
        "leads_ignorados": 0,
        "leads_repetidos": 0,
        "erros_lead": 0,
        "confirmacoes_enviadas": 0,
        "registros_unicos": 0,
        "supabase_registros": 0,
        "preview_processados": [],
    }

    try:
        driver = fazer_login(URL_LOGIN, USUARIO, SENHA)
        tenant_id = obter_tenant_id(driver)
        resumo_execucao["tenant_id"] = tenant_id

        if not tenant_id:
            resumo_execucao["status"] = "error"
            resumo_execucao["erro"] = "Tenant ID ausente."
            print("Erro de tenant id.")
            return 1

        dados_completos, resumo_processamento = processar_e_coletar_leads(driver, URL_CLIENTES, tenant_id, TEXTO_CONFIRMACAO)
        resumo_execucao.update(resumo_processamento)
        if not dados_completos:
            resumo_execucao["status"] = "no_data"
            print("\nNenhum dado valido para salvar.")
            return 0

        df_clientes = pd.DataFrame(dados_completos)
        df_clientes.drop_duplicates(subset=["telefone"], keep="last", inplace=True)
        resumo_execucao["registros_unicos"] = len(df_clientes)

        print("\n--- Preview dos Dados ---")
        print(df_clientes[["nome", "telefone", "mensagem_enviada"]].head().to_string(index=False))
        resumo_execucao["preview_processados"] = [
            f"{linha.nome} ({linha.telefone})"
            for linha in df_clientes[["nome", "telefone"]].head().itertuples(index=False)
        ]

        dados_limpos = df_clientes.to_dict("records")
        resumo_execucao["supabase_registros"] = salvar_no_supabase(SUPABASE_URL, SUPABASE_KEY, dados_limpos)
        return 0
    except Exception as exc:
        resumo_execucao["status"] = "error"
        resumo_execucao["erro"] = formatar_erro(exc)
        print(f"\nErro geral: {exc}")
        return 1
    finally:
        resumo_execucao["fim_utc"] = utc_now()
        resumo_execucao["duracao_segundos"] = (
            resumo_execucao["fim_utc"] - resumo_execucao["inicio_utc"]
        ).total_seconds()

        relatorio_texto = montar_relatorio_execucao(resumo_execucao)

        print("\nEncerrando...")
        if driver:
            driver.quit()

        try:
            enviar_relatorio_evolution(relatorio_texto)
        except Exception as exc:
            print(f"Falha ao enviar relatorio pela Evolution API: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
