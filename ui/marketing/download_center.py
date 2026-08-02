import os
import streamlit as st


def get_file_label(file_key):
    labels = {
        "strategy_docx": "Strategy DOCX",
        "calendar_xlsx": "Content Calendar XLSX",
        "reel_scripts_docx": "Reel Scripts DOCX",
        "captions_docx": "Captions DOCX",
        "hashtags_txt": "Hashtags TXT",
        "image_prompts_txt": "Image Prompts TXT",
        "meta_ads_docx": "Meta Ads DOCX",
    }

    return labels.get(file_key, file_key)


def get_mime_type(file_path):
    if file_path.endswith(".docx"):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    if file_path.endswith(".xlsx"):
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    if file_path.endswith(".txt"):
        return "text/plain"

    return "application/octet-stream"


def show_download_center(deliverables):
    st.subheader("⬇️ Download Center")

    if not deliverables:
        st.info("No downloadable files available.")
        return

    files = deliverables.get("files", {})

    if not files:
        st.info("No files found.")
        return

    for file_key, file_path in files.items():
        if not os.path.exists(file_path):
            st.warning(f"{get_file_label(file_key)} file missing.")
            continue

        with open(file_path, "rb") as file:
            st.download_button(
                label=f"Download {get_file_label(file_key)}",
                data=file,
                file_name=os.path.basename(file_path),
                mime=get_mime_type(file_path),
                use_container_width=True,
                key=f"download_{file_key}_{file_path}"
            )