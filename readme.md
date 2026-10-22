# 🎵 Scrappy Mappy — Scraper y Analizador de IA para TikTok

**Scrappy Mappy** es una herramienta académica que combina técnicas de _web scraping_ y modelos de lenguaje (LLMs) para extraer métricas públicas de perfiles de TikTok y realizar un análisis de sentimiento, emociones y personalidad (Big Five) sobre el contenido y la audiencia.

El proyecto está diseñado con una arquitectura híbrida y robusta, capaz de evadir bloqueos comunes y ofreciendo una interfaz web amigable para el usuario final.

---

## 🚀 Características Principales

1. **Interfaz Web Interactiva:** Desarrollada con Streamlit, permite buscar usuarios, visualizar métricas y descargar resultados sin tocar la línea de comandos.
2. **Scraping Híbrido:** Utiliza Playwright para evadir el _login wall_ y extraer los datos del perfil, y `yt-dlp` para extraer las métricas y comentarios de los videos (evitando bloqueos HTTP 403).
3. **Persistencia de Sesión:** Guarda las cookies y el estado del navegador (`session.json`) para evitar pedir captchas en cada búsqueda.
4. **Análisis con Inteligencia Artificial:** Utiliza la API de **Groq** (modelo `gpt-oss-120b`) para analizar descripciones y comentarios, detectando el sentimiento, la emoción predominante y los 5 grandes rasgos de personalidad (Big Five).
5. **Visualización de Datos:** Genera un panel de control con 6 gráficos (pasteles, radar, barras y mapas de calor) comparando la "personalidad" del creador con la de su audiencia.

---

## 🛠️ Tecnologías Utilizadas

- **Playwright:** Automatización del navegador (Chromium) para manejar la carga dinámica de TikTok y resolución asistida de captchas.
- **yt-dlp + curl_cffi:** Extracción de metadatos y comentarios imitando la firma TLS de Chrome (para evitar el error 403 Forbidden).
- **Streamlit:** Creación de la interfaz web (Frontend).
- **Groq API:** Análisis de texto con el modelo de lenguaje `openai/gpt-oss-120b`.
- **Matplotlib & Pandas:** Procesamiento de datos y generación de gráficos estadísticos.

---

## 📋 Requisitos Previos

- Python 3.10 o superior.
- Una cuenta gratuita en [Groq](https://console.groq.com/) para obtener la API Key del análisis de IA.

---

## ⚙️ Instalación y Configuración

### 1. Clonar el repositorio e instalar dependencias

```bash
git clone https://github.com/Jettro12/Scrappy-Mappy.git
cd Scrappy-Mappy
python -m venv .venv
# Activar entorno (Windows)
.\.venv\Scripts\Activate.ps1
# Instalar dependencias
pip install -r requirements.txt
python -m playwright install chromium
```
