import streamlit as st
import pandas as pd
import pdfplumber
import re
import sqlite3
import uuid
import socket

# --- 1. CONFIGURAÇÃO E CSS ---
st.set_page_config(page_title="SplitBill - Divisor", page_icon="💳", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
    <style>
    #MainMenu {visibility: hidden !important;}
    header {visibility: hidden !important;}
    footer {visibility: hidden !important;}
    [data-testid="stToolbar"] {display: none !important;} /* Oculta a barra de ferramentas nativa (Deploy) */
    a.header-anchor { display: none !important; }
    section[data-testid="stSidebar"] { width: 250px !important; min-width: 250px !important; max-width: 250px !important; }
    section[data-testid="stSidebar"] > div { overflow-y: hidden !important; }
    .block-container { padding: 2rem 3rem !important; }
    </style>
""", unsafe_allow_html=True)

# --- 2. DESCOBERTA AUTOMÁTICA DE IP LOCAL ---
@st.cache_data
def obter_ip_maquina():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80)) 
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "localhost"

IP_ATUAL = obter_ip_maquina()
PORTA = "8501" 

# --- 3. BANCO DE DADOS (SQLite) ---
DB_NAME = "faturas.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS faturas (id TEXT PRIMARY KEY, nome TEXT, total REAL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS itens 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, fatura_id TEXT, data TEXT, descricao TEXT, valor REAL, dono TEXT)''')
    conn.commit()
    conn.close()

def salvar_nova_fatura(fatura_id, nome_fatura, itens, total):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT INTO faturas (id, nome, total) VALUES (?, ?, ?)", (fatura_id, nome_fatura, total))
    for item in itens:
        c.execute("INSERT INTO itens (fatura_id, data, descricao, valor, dono) VALUES (?, ?, ?, ?, ?)",
                  (fatura_id, item['Data'], item['Descrição'], item['Valor (R$)'], ""))
    conn.commit()
    conn.close()

def listar_faturas_salvas():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, nome FROM faturas")
    linhas = c.fetchall()
    conn.close()
    return {nome: id for id, nome in linhas}

def carregar_itens(fatura_id):
    conn = sqlite3.connect(DB_NAME)
    df = pd.read_sql_query(f"SELECT id, data as Data, descricao as 'Descrição', valor as 'Valor (R$)', dono as 'Dono da Compra' FROM itens WHERE fatura_id = '{fatura_id}'", conn)
    conn.close()
    return df

def atualizar_banco(fatura_id, df_editado):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    for _, row in df_editado.iterrows():
        c.execute("UPDATE itens SET dono = ? WHERE id = ?", (row['Dono da Compra'], row['id']))
    conn.commit()
    conn.close()

init_db()

# --- 4. MOTOR DE EXTRAÇÃO ---
def extrair_fatura(arquivo_pdf, senha=""):
    padroes = [
        re.compile(r"^(\d{2}\s[A-Z]{3})\s+(?:••••\s+\d{4}\s+)?(.+?)\s+R\$\s+([\d\.]+,\d{2})$", re.IGNORECASE),
        re.compile(r"^(\d{2}\sde\s[a-zA-Z]{3}\.?\s\d{4})\s+(.+?)\s+(?:-\s+)?R\$\s+([\d\.]+,\d{2})$", re.IGNORECASE)
    ]
    compras = []
    try:
        with pdfplumber.open(arquivo_pdf, password=senha) as pdf:
            for pagina in pdf.pages:
                texto = pagina.extract_text()
                if not texto: continue
                for linha in texto.split('\n'):
                    linha_limpa = linha.strip()
                    if "PAGAMENTO" in linha_limpa.upper() or "+ R$" in linha_limpa: continue
                    for padrao in padroes:
                        match = padrao.match(linha_limpa)
                        if match:
                            compras.append({
                                "Data": match.group(1),
                                "Descrição": match.group(2).strip(),
                                "Valor (R$)": float(match.group(3).replace('.', '').replace(',', '.')),
                            })
                            break
        return compras
    except Exception as e:
        if "password" in str(e).lower(): return "erro_senha"
        return "erro_generico"

def renderizar_resumo_fechamento(df_editado):
    compras_atribuidas = df_editado[df_editado["Dono da Compra"].astype(str).str.strip() != ""]
    if not compras_atribuidas.empty:
        registros = []
        for _, row in compras_atribuidas.iterrows():
            nomes = [n.strip() for n in str(row["Dono da Compra"]).split(",") if n.strip()]
            for nome in nomes:
                registros.append({"Pessoa": nome, "Valor": row["Valor (R$)"] / len(nomes)})
        
        resumo = pd.DataFrame(registros).groupby("Pessoa")["Valor"].sum().reset_index()
        c1, c2 = st.columns([1, 1])
        # Corrigido o aviso do terminal atualizando o parâmetro para width="stretch"
        c1.dataframe(resumo.style.format({"Valor": "R$ {:.2f}"}), width="stretch", hide_index=True)
        c2.bar_chart(resumo.set_index("Pessoa"), y="Valor")
    else:
        st.warning("Nenhum nome atribuído nesta fatura ainda.")

# --- 5. ROTEAMENTO DE PÁGINAS ---
query_params = st.query_params
fatura_atual_id = query_params.get("id")

st.title("💳 SplitBill")

if fatura_atual_id:
    df_banco = carregar_itens(fatura_atual_id)
    
    if df_banco.empty:
        st.error("⚠️ Link inválido ou fatura não encontrada.")
    else:
        st.markdown("##### Preencha seu nome na coluna 'Dono da Compra' e clique em Salvar.")
        # Corrigido o aviso do terminal atualizando o parâmetro para width="stretch"
        df_editado = st.data_editor(df_banco, num_rows="fixed", width="stretch", hide_index=True, disabled=["id", "Data", "Descrição", "Valor (R$)"])
        
        if st.button("💾 Salvar Minhas Alterações", type="primary"):
            atualizar_banco(fatura_atual_id, df_editado)
            st.success("Alterações salvas com sucesso!")
            st.rerun()

        st.divider()
        st.subheader("💰 Resumo Parcial do Fechamento")
        renderizar_resumo_fechamento(df_editado)

else:
    with st.sidebar:
        st.markdown('<div style="text-align: center;"><img src="https://cdn-icons-png.flaticon.com/512/2830/2830284.png" width="60" style="pointer-events: none;"></div>', unsafe_allow_html=True)
        st.markdown("### Autenticação")
        
        SENHA_MEU_SISTEMA = "admin123" 
        senha_admin_digitada = st.text_input("Senha do Sistema", type="password")
        
        st.write("---")
        st.caption("🚀 **Leonardo Araújo** | 📅 **2026**")

    if senha_admin_digitada == SENHA_MEU_SISTEMA:
        aba_upload, aba_historico = st.tabs(["📥 Upload de Nova Fatura", "🗄️ Histórico de Faturas Salvas"])
        
        with aba_upload:
            st.markdown("### Processar Novo PDF")
            nome_fatura = st.text_input("Dê um nome/apelido para esta fatura")
            senha_pdf = st.text_input("Senha do PDF do banco (se houver)", type="password")
            arquivo_fatura = st.file_uploader("Selecione o arquivo PDF", type=["pdf"])
            
            if arquivo_fatura is not None:
                dados = extrair_fatura(arquivo_fatura, senha_pdf)
                if dados == "erro_senha":
                    st.error("🔒 Senha do PDF incorreta.")
                elif isinstance(dados, list) and len(dados) > 0:
                    df = pd.DataFrame(dados)
                    total_fatura = df['Valor (R$)'].sum()
                    st.success(f"PDF processado com sucesso! Total: R$ {total_fatura:,.2f}")
                    
                    if st.button("🚀 Salvar no Banco e Gerar Link", type="primary"):
                        if nome_fatura.strip() == "":
                            st.error("⚠️ Por favor, digite um nome para a fatura antes de gerar o link.")
                        else:
                            novo_id = str(uuid.uuid4())[:8]
                            salvar_nova_fatura(novo_id, nome_fatura.strip(), dados, total_fatura)
                            
                            link = f"http://{IP_ATUAL}:{PORTA}/?id={novo_id}"
                            st.info("Fatura salva de forma definitiva! Envie este link para os usuários preencherem:")
                            st.code(link, language="text")
                else:
                    st.error("Nenhuma transação encontrada no arquivo.")
                    
        with aba_historico:
            st.markdown("### Gerenciar Faturas Armazenadas")
            dict_faturas = listar_faturas_salvas()
            
            if not dict_faturas:
                st.info("Nenhuma fatura foi salva no banco de dados ainda.")
            else:
                fatura_selecionada_nome = st.selectbox("Escolha a fatura que deseja inspecionar:", list(dict_faturas.keys()))
                id_selecionado = dict_faturas[fatura_selecionada_nome]
                df_historico = carregar_itens(id_selecionado)
                
                st.write(f"Mostrando dados de: **{fatura_selecionada_nome}**")
                # Corrigido o aviso do terminal atualizando o parâmetro para width="stretch"
                df_hist_editado = st.data_editor(df_historico, num_rows="fixed", width="stretch", hide_index=True, disabled=["id", "Data", "Descrição", "Valor (R$)"])
                
                c_salvar, c_link = st.columns([1, 3])
                if c_salvar.button("💾 Atualizar Banco", key="btn_admin_save"):
                    atualizar_banco(id_selecionado, df_hist_editado)
                    st.success("Banco de dados atualizado pelo administrador!")
                    st.rerun()
                    
                c_link.text(f"🔗 Link desta fatura: http://{IP_ATUAL}:{PORTA}/?id={id_selecionado}")
                
                st.divider()
                st.markdown("#### 📊 Divisão de Gastos Atual")
                renderizar_resumo_fechamento(df_hist_editado)
                
    elif senha_admin_digitada != "":
        st.error("Senha de acesso administrativa incorreta.")
    else:
        st.info("💡 Por favor, insira a Senha do Sistema na barra lateral à esquerda para acessar a Área Administrativa.")