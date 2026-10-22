"""
app.py — Interfaz web (Streamlit) autónoma para Scrappy_Mappy.
Ejecutar con:  streamlit run app.py
"""

import io
import json
from pathlib import Path

import pandas as pd
import streamlit as st

from Scrappy_Mappy import scrape_tiktok

st.set_page_config(
    page_title="Scrappy Mappy — TikTok",
    page_icon="🎵",
    layout="wide",
)

st.title("🎵 Scrappy Mappy — Análisis de TikTok")
st.caption("Herramienta académica para extraer métricas públicas de perfiles de TikTok.")


# ---------------- Barra lateral ----------------
with st.sidebar:
    st.header("⚙️ Configuración")
    max_videos = st.slider("Número de videos", 1, 20, 10)
    with_comments = st.checkbox("Extraer comentarios", value=False,
                                help="Más lento. Necesario para el análisis de audiencia.")
    
    st.divider()
    if Path("session.json").exists():
        st.success("✅ Sesión activa (rápido y sin ventanas)")
    else:
        st.info(
            "ℹ️ **No hay sesión guardada.**\n\n"
            "La primera búsqueda abrirá una ventana de Chromium para que resuelvas el captcha. "
            "Después de eso, las búsquedas serán instantáneas."
        )
    
    st.divider()
    st.markdown("**📝 Consejos**")
    st.markdown("- Resuelve el captcha en la ventana que se abra.")
    st.markdown("- No hagas más de 3 búsquedas seguidas.")
    st.markdown("- Si falla, espera 15-30 min.")


# ---------------- Formulario ----------------
col_input, col_btn = st.columns([4, 1])
with col_input:
    username = st.text_input("Usuario de TikTok (sin @)", placeholder="nasa",
                             label_visibility="collapsed")
with col_btn:
    buscar = st.button("🔍 Buscar", type="primary", use_container_width=True)


# ---------------- Ejecución ----------------
if buscar:
    user_clean = (username or "").strip().lstrip("@")
    if not user_clean:
        st.error("Escribe un nombre de usuario.")
    else:
        # Si no hay sesión, abrimos el navegador para el captcha (headless=False)
        # Si ya hay sesión, todo ocurre en segundo plano (headless=True)
        session_exists = Path("session.json").exists()
        modo_headless = session_exists
        
        if not session_exists:
            st.warning(
                "⚠️ **Primera ejecución.**\n\n"
                "Se abrirá una ventana de Chromium en tu pantalla. "
                "Por favor, resuelve el captcha ahí y no cierres la ventana. "
                "La web continuará automáticamente cuando termines."
            )
            
        with st.spinner(f"Scrapeando @{user_clean}... (puede tardar 1-2 minutos)"):
            try:
                result = scrape_tiktok(
                    user_clean,
                    headless=modo_headless,
                    max_videos=max_videos,
                    with_comments=with_comments,
                )
                st.session_state["result"] = result
                st.session_state["username"] = user_clean
                st.rerun()  # Refrescar la interfaz para quitar el mensaje de advertencia
            except Exception as e:
                st.error(f"Error inesperado: {e}")


# ---------------- Resultados ----------------
if "result" in st.session_state:
    result = st.session_state["result"]
    user_shown = st.session_state.get("username", "usuario")
    profile = result.get("profile")

    st.divider()
    st.subheader(f"📊 Resultados de @{user_shown}")
    if result.get("scraped_at"):
        st.caption(f"Extraído el {result['scraped_at']}")

    # --- Tarjetas del perfil ---
    if profile:
        st.markdown(f"**{profile.get('nickname','')}** · `@{profile.get('username', user_shown)}`")
        if profile.get("bio"):
            st.info(f"📝 {profile['bio']}")
        
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("👥 Seguidores", f"{profile.get('followers',0):,}")
        c2.metric("👤 Seguidos", f"{profile.get('following',0):,}")
        c3.metric("❤️ Likes totales", f"{profile.get('likes_total',0):,}")
        c4.metric("🎬 Videos", f"{profile.get('video_count',0):,}")
    else:
        st.warning("No se pudo extraer el perfil.")

    # --- Tabla de videos ---
    videos = result.get("videos", [])
    if videos:
        st.markdown("### 🎬 Métricas de los videos")
        df = pd.DataFrame(videos)
        
        # Seleccionar columnas a mostrar
        cols_mostrar = ["url", "likes", "comments", "shares", "views", "description"]
        cols_mostrar = [c for c in cols_mostrar if c in df.columns]
        df_show = df[cols_mostrar].copy()
        
        st.dataframe(
            df_show,
            use_container_width=True,
            column_config={
                "url": st.column_config.LinkColumn("Video"),
                "likes": st.column_config.NumberColumn("❤️ Likes", format="%d"),
                "comments": st.column_config.NumberColumn("💬 Comentarios", format="%d"),
                "shares": st.column_config.NumberColumn("🔁 Shares", format="%d"),
                "views": st.column_config.NumberColumn("👁 Vistas", format="%d"),
                "description": st.column_config.TextColumn("Descripción", width="large"),
            },
            hide_index=True,
        )

        # --- Gráfico de barras comparativo ---
        if all(c in df.columns for c in ["likes", "comments", "shares"]):
            st.markdown("#### 📈 Comparativa de interacciones")
            st.bar_chart(df[["likes", "comments", "shares"]])

        # --- Descargas ---
        st.markdown("#### ⬇️ Descargar resultados")
        col_json, col_csv = st.columns(2)
        
        json_str = json.dumps(result, indent=2, ensure_ascii=False)
        csv_buf = io.StringIO()
        df.to_csv(csv_buf, index=False)
        
        col_json.download_button(
            "📥 JSON completo",
            data=json_str,
            file_name=f"scrappy_mapy_{user_shown}.json",
            mime="application/json",
            use_container_width=True,
        )
        col_csv.download_button(
            "📥 CSV de videos",
            data=csv_buf.getvalue(),
            file_name=f"scrappy_mapy_{user_shown}.csv",
            mime="text/csv",
            use_container_width=True,
        )

    # --- Comentarios ---
    if with_comments and videos:
        comentarios_totales = sum(len(v.get("top_comments", [])) for v in videos)
        if comentarios_totales > 0:
            st.success(f"✅ Se extrajeron {comentarios_totales} comentarios. El JSON ya está listo para `analyze_groq.py`.")
        else:
            st.warning("No se pudieron extraer comentarios (posible bloqueo o `yt-dlp` no los devolvió).")

    # --- Errores ---
    if result.get("errors"):
        with st.expander(f"⚠️ {len(result['errors'])} incidencias"):
            for e in result["errors"]:
                st.text(e)