import os
import streamlit as st


def show_download_center(deliverables):

    st.subheader("⬇ Download Deliverables")

    if not deliverables:
        st.info("No deliverables generated.")
        return

    files = deliverables.get("files", {})

    for key, path in files.items():

        if not os.path.exists(path):
            continue

        with open(path, "rb") as file:

            st.download_button(
                f"Download {os.path.basename(path)}",
                data=file,
                file_name=os.path.basename(path),
                use_container_width=True,
                key=path
            )