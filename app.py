import streamlit as st
import os
from langchain_community.document_loaders import PyPDFDirectoryLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.chains import RetrievalQA

# --- UI Setup (Gemini Style) ---
st.set_page_config(page_title="Benefix AI", layout="centered")
st.title("🎓 Benefix Scholarship AI")
st.markdown("Ask anything about the 2026 scholarship datasets.")

# User must provide Gemini API Key
api_key = st.sidebar.text_input("Enter Google API Key", type="password")

if api_key:
    # 1. Load and Process Data
    @st.cache_resource
    def initialize_rag():
        loader = PyPDFDirectoryLoader("./scholarship_pdfs")
        docs = loader.load()
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=100)
        chunks = text_splitter.split_documents(docs)
        
        embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
        vector_db = FAISS.from_documents(chunks, embeddings)
        return vector_db

    vector_db = initialize_rag()
    
    # 2. Setup Gemini Model
    llm = ChatGoogleGenerativeAI(model="gemini-1.5-flash", google_api_key=api_key)
    qa_chain = RetrievalQA.from_chain_type(
        llm=llm, 
        chain_type="stuff", 
        retriever=vector_db.as_retriever(search_kwargs={"k": 3})
    )

    # 3. Chat Interface
    if "messages" not in st.session_state:
        st.session_state.messages = []

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    if prompt := st.chat_input("How can I help you with scholarships?"):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            response = qa_chain.invoke(prompt)
            st.markdown(response["result"])
            st.session_state.messages.append({"role": "assistant", "content": response["result"]})
else:
    st.info("Please add your Google API key in the sidebar to begin.")
