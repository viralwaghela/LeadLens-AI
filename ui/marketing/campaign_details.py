import pandas as pd
import streamlit as st

from ui.marketing.download_center import show_download_center


def show_strategy(strategy):
    st.subheader("📌 Strategy")

    col1, col2 = st.columns(2)

    with col1:
        st.write("**Objective**")
        st.info(strategy.get("objective", "N/A"))

        st.write("**Target Audience**")
        st.info(strategy.get("target_audience", "N/A"))

        st.write("**Primary CTA**")
        st.success(strategy.get("primary_cta", "N/A"))

    with col2:
        st.write("**Positioning**")
        st.info(strategy.get("positioning", "N/A"))

        st.write("**Offer**")
        st.warning(strategy.get("offer", "N/A"))

    st.write("**Pain Points**")
    for point in strategy.get("pain_points", []):
        st.write(f"• {point}")


def show_calendar(calendar):
    st.subheader("📅 Content Calendar")

    if calendar:
        st.dataframe(pd.DataFrame(calendar), use_container_width=True)
    else:
        st.info("No calendar generated.")


def show_reels(reels):
    st.subheader("🎬 Reel Ideas")

    if not reels:
        st.info("No reels generated.")
        return

    for reel in reels:
        with st.expander(reel.get("title", "Untitled Reel")):
            st.write("**Hook**")
            st.info(reel.get("hook", ""))

            st.write("**Script**")
            st.write(reel.get("script", ""))

            st.write("**CTA**")
            st.success(reel.get("cta", ""))


def show_captions(captions):
    st.subheader("✍️ Captions")

    if not captions:
        st.info("No captions generated.")
        return

    for caption in captions:
        with st.expander(caption.get("platform", "Platform")):
            st.write(caption.get("caption", ""))


def show_prompts(prompts):
    st.subheader("🖼️ Image Prompts")

    if not prompts:
        st.info("No image prompts generated.")
        return

    for prompt in prompts:
        with st.expander(prompt.get("title", "Prompt")):
            st.write(prompt.get("prompt", ""))


def show_ads(ads):
    st.subheader("📢 Meta Ads")

    if not ads:
        st.info("No ads generated.")
        return

    for index, ad in enumerate(ads, start=1):
        with st.expander(f"Meta Ad {index}"):
            st.write("**Primary Text**")
            st.write(ad.get("primary_text", ""))

            st.write("**Headline**")
            st.info(ad.get("headline", ""))

            st.write("**Description**")
            st.write(ad.get("description", ""))

            st.write("**CTA**")
            st.success(ad.get("cta", ""))


def show_kpis(kpis):
    st.subheader("📊 KPIs")

    col1, col2, col3 = st.columns(3)

    col1.metric("Expected Reach", kpis.get("expected_reach", "N/A"))
    col2.metric("Expected Leads", kpis.get("expected_leads", "N/A"))
    col3.metric("Success Metric", kpis.get("success_metric", "N/A"))


def show_campaign_details(entry):
    data = entry.get("data", {})
    task = data.get("task", {})
    output = data.get("output", {})
    deliverables = data.get("deliverables", {})

    campaign_name = output.get("campaign_name", task.get("title", "Untitled Campaign"))

    st.markdown(f"# {campaign_name}")
    st.caption(f"Created: {entry.get('created_at', '')}")
    st.info(f"Generated from task: {task.get('title', 'N/A')}")

    if "error" in output:
        st.error(output.get("error"))
        st.text_area("Raw Response", output.get("raw_response", ""), height=300)
        return

    tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
        "Strategy",
        "Calendar",
        "Reels",
        "Captions",
        "Image Prompts",
        "Ads",
        "Downloads"
    ])

    with tab1:
        show_strategy(output.get("strategy", {}))
        show_kpis(output.get("kpis", {}))

    with tab2:
        show_calendar(output.get("content_calendar", []))

    with tab3:
        show_reels(output.get("reel_ideas", []))

    with tab4:
        show_captions(output.get("captions", []))
        hashtags = output.get("hashtags", [])
        if hashtags:
            st.subheader("#️⃣ Hashtags")
            st.code(" ".join(hashtags))

    with tab5:
        show_prompts(output.get("image_prompts", []))

    with tab6:
        show_ads(output.get("meta_ads", []))

    with tab7:
        show_download_center(deliverables)