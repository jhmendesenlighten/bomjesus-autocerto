import os
import re
import shutil
import time

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


def ler_env_bool(nome_variavel, padrao=False):
    valor = os.getenv(nome_variavel)
    if valor is None:
        return padrao

    return valor.strip().lower() in {"1", "true", "t", "yes", "y", "on"}


HEADLESS = ler_env_bool(
    "HEADLESS",
    padrao=bool(os.getenv("RAILWAY_ENVIRONMENT_NAME") or os.getenv("RAILWAY_ENVIRONMENT_ID")),
)


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


def criar_driver_chrome():
    print(f"Iniciando o navegador (headless={'sim' if HEADLESS else 'nao'})...")

    options = webdriver.ChromeOptions()
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--remote-debugging-pipe")
    options.add_argument("--window-size=1920,1080")

    if HEADLESS:
        options.add_argument("--headless=new")

    chrome_bin = encontrar_binario_chrome()
    if chrome_bin:
        options.binary_location = chrome_bin

    chromedriver_path = os.getenv("CHROMEDRIVER_PATH") or shutil.which("chromedriver")
    service = ChromeService(executable_path=chromedriver_path) if chromedriver_path else ChromeService()

    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(60)
    return driver


def fazer_login(url, usuario, senha):
    driver = criar_driver_chrome()
    wait = WebDriverWait(driver, WAIT_TIMEOUT)

    print(f"Acessando a pagina de login: {url}")
    driver.get(url)

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

    print("\n--- Buscando lista de Leads ---")

    def carregar_lista_leads():
        driver.get(url)
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

        if num_total_leads == 0:
            print(f"Nenhum lead encontrado com a classe '{LISTA_LEADS_CLASS}'.")
            return []

        print(f"Contagem detectada: {num_total_leads} leads para processar.")
    except Exception as exc:
        print(f"Erro ao carregar a lista inicial: {exc}")
        return []

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
            print(" -> Lead finalizado e salvo na lista temporaria.")
        except Exception as exc:
            if assinatura_atual:
                assinaturas_tentadas.add(assinatura_atual)
            print(f"Erro no lead {numero_execucao}: {exc}")
            continue

    print("\n--- Processo concluido. ---")
    return dados_coletados


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
    try:
        driver = fazer_login(URL_LOGIN, USUARIO, SENHA)
        tenant_id = obter_tenant_id(driver)

        if not tenant_id:
            print("Erro de tenant id.")
            return 1

        dados_completos = processar_e_coletar_leads(driver, URL_CLIENTES, tenant_id, TEXTO_CONFIRMACAO)
        if not dados_completos:
            print("\nNenhum dado valido para salvar.")
            return 0

        df_clientes = pd.DataFrame(dados_completos)
        df_clientes.drop_duplicates(subset=["telefone"], keep="last", inplace=True)

        print("\n--- Preview dos Dados ---")
        print(df_clientes[["nome", "telefone", "mensagem_enviada"]].head().to_string(index=False))

        dados_limpos = df_clientes.to_dict("records")
        salvar_no_supabase(SUPABASE_URL, SUPABASE_KEY, dados_limpos)
        return 0
    except Exception as exc:
        print(f"\nErro geral: {exc}")
        return 1
    finally:
        print("\nEncerrando...")
        if driver:
            driver.quit()


if __name__ == "__main__":
    raise SystemExit(main())
