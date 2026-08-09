import streamlit as st
import os
from huggingface_hub import snapshot_download
from langchain_community.document_loaders import PyPDFDirectoryLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser

# --- Page Config ---
st.set_page_config(page_title="Benefix AI", page_icon="🎓", layout="wide")

st.markdown("""
    <style>
    .stApp { background-color: #f8fafd; }
    .stChatMessage { border-radius: 15px; margin-bottom: 10px; }
    </style>
    """, unsafe_allow_html=True)

st.title("🎓 Benefix Scholarship AI")
st.caption("Powered by Gemini 1.5 & Sushant-X7/Scholarships-2026")

with st.sidebar:
    st.header("Settings")
    api_key = st.text_input("Enter Gemini API Key", type="password")
    st.divider()
    st.info("The AI reads from your Hugging Face dataset automatically.")

# --- Data Processing ---
@st.cache_resource
def load_and_index_data():
    repo_id = "Sushant-X7/Scholarships-2026"
    local_dir = "./scholarship_pdfs"
    # Downloads PDFs
    snapshot_download(repo_id=repo_id, local_dir=local_dir, repo_type="dataset", allow_patterns="*.pdf")
    
    loader = PyPDFDirectoryLoader(local_dir)
    docs = loader.load()
    
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=150)
    chunks = text_splitter.split_documents(docs)
    
    embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
    vector_db = FAISS.from_documents(chunks, embeddings)
    return vector_db

# --- AI Logic ---
if api_key:
    try:
        with st.spinner("Processing Documents..."):
            vector_db = load_and_index_data()
            retriever = vector_db.as_retriever(search_kwargs={"k": 5})

        llm = ChatGoogleGenerativeAI(model="gemini-1.5-flash", google_api_key=api_key)

        # Modern RAG Prompt
        template = """Answer the question based only on the following context:
        {context}
        
        Question: {question}
        """
        prompt = ChatPromptTemplate.from_template(template)

        # Modern RAG Chain (LCEL)
        def format_docs(docs):
            return "\n\n".join(doc.page_content for doc in docs)

        rag_chain = (
            {"context": retriever | format_docs, "question": RunnablePassthrough()}
            | prompt
            | llm
            | StrOutputParser()
        )

        # Chat History UI
        if "messages" not in st.session_state:
            st.session_state.messages = []

        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

        if user_query := st.chat_input("Ask about scholarships..."):
            st.session_state.messages.append({"role": "user", "content": user_query})
            with st.chat_message("user"):
                st.markdown(user_query)

            with st.chat_message("assistant"):
                with st.spinner("Searching documents..."):
                    response = rag_chain.invoke(user_query)
                    st.markdown(response)
                    st.session_state.messages.append({"role": "assistant", "content": response})

    except Exception as e:
        st.error(f"Error: {e}")
else:
    st.warning("Please enter your API Key in the sidebar.")
