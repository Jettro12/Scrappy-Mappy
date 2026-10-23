"""
analyze_groq.py
---------------
Analiza el JSON más reciente generado por Scrappy_Mappy.py usando Groq.
"""

import argparse
import glob
import json
import os
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from groq import Groq

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

client = Groq(api_key=os.getenv("GROQ_API_KEY"))
MODELO = "openai/gpt-oss-120b"

RASGOS = ["apertura", "responsabilidad", "extraversion", "amabilidad", "neuroticismo"]
COLORES_SENT = {
    "positivo": "#4CAF50",
    "negativo": "#F44336",
    "neutral":  "#9E9E9E",
    "mixto":    "#FFC107",
}   # <-- FIX: eliminada la "ñ" suelta que rompía el archivo
TRADUCCION_EMOCIONES = {
    "joy":      "Alegría",
    "sadness":  "Tristeza",
    "anger":    "Ira",
    "fear":     "Miedo",
    "surprise": "Sorpresa",
    "disgust":  "Asco",
    "neutral":  "Neutral",
}


def encontrar_json_reciente(prefijo="scrappy_mapy_*.json"):
    archivos = glob.glob(prefijo)
    if not archivos:
        return None
    return max(archivos, key=os.path.getmtime)


def analizar_texto(texto, tipo):
    if not texto or not texto.strip():
        return None

    prompt = f"""Analiza el siguiente texto extraído de los {tipo} de un video de TikTok.

--- TEXTO ---
{texto}

--- INSTRUCCIONES ---
Devuelve ÚNICAMENTE un objeto JSON válido con esta estructura:
{{
  "sentimiento": "positivo" | "negativo" | "neutral" | "mixto",
  "emocion": "joy" | "sadness" | "anger" | "fear" | "surprise" | "disgust" | "neutral",
  "big_five": {{
    "apertura": 0.0,
    "responsabilidad": 0.0,
    "extraversion": 0.0,
    "amabilidad": 0.0,
    "neuroticismo": 0.0
  }}
}}
Cada valor de big_five debe ser un float entre 0.0 y 1.0.
Devuelve únicamente el JSON válido."""

    try:
        completion = client.chat.completions.create(
            model=MODELO,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            response_format={"type": "json_object"},
        )

        resultado = json.loads(completion.choices[0].message.content)

        big_five = resultado.get("big_five", {})

        individuales = {
            rasgo: round(max(0.0, min(1.0, float(big_five.get(rasgo, 0.0)))) * 100, 2)
            for rasgo in RASGOS
        }

        total = sum(individuales.values())

        if total > 0:
            normalizados = {
                rasgo: round(valor / total * 100, 2)
                for rasgo, valor in individuales.items()
            }
            diferencia = round(100 - sum(normalizados.values()), 2)
            mayor = max(normalizados, key=normalizados.get)
            normalizados[mayor] += diferencia
        else:
            normalizados = {rasgo: 0.0 for rasgo in RASGOS}

        resultado["big_five_individual"] = individuales
        resultado["big_five_normalizado"] = normalizados

        return resultado

    except Exception as e:
        print(f"  [error] {tipo}: {e}")
        return None


def graficar_todo(resultados, username):
    desc = [r["descripcion"] for r in resultados if r.get("descripcion")]
    com  = [r["comentarios"] for r in resultados if r.get("comentarios")]

    fig = plt.figure(figsize=(18, 16), constrained_layout=True)
    fig.suptitle(f"Análisis de @{username} — {len(resultados)} videos",
                 fontsize=16, fontweight="bold")

    ax1 = fig.add_subplot(3, 2, 1)
    _graficar_sentimiento(ax1, [d["sentimiento"] for d in desc],
                          "Sentimiento — Descripciones del creador")

    ax2 = fig.add_subplot(3, 2, 2)
    _graficar_sentimiento(ax2, [c["sentimiento"] for c in com],
                          "Sentimiento — Comentarios de la audiencia")

    ax3 = fig.add_subplot(3, 2, 3, projection="polar")
    _graficar_radar(ax3, desc, com)

    ax4 = fig.add_subplot(3, 2, 4)
    _graficar_emociones(ax4, desc, com)

    ax5 = fig.add_subplot(3, 2, 5)
    _graficar_heatmap(ax5, desc, "Big Five por video — Descripciones")

    ax6 = fig.add_subplot(3, 2, 6)
    _graficar_heatmap(ax6, com, "Big Five por video — Comentarios")

    fig.savefig(f"analisis_{username}.png", dpi=130, bbox_inches="tight")
    print(f"\n[gráfico] Guardado en analisis_{username}.png")
    plt.show()


def _graficar_sentimiento(ax, sentimientos, titulo):
    ax.set_title(titulo, pad=15)
    if not sentimientos:
        ax.text(0.5, 0.5, "Sin datos", ha="center", va="center", fontsize=12, color="gray")
        ax.axis("off")
        return
    conteo = {}
    for s in sentimientos:
        conteo[s] = conteo.get(s, 0) + 1
    labels = list(conteo.keys())
    values = list(conteo.values())
    colors = [COLORES_SENT.get(k, "#9E9E9E") for k in labels]
    ax.pie(values, labels=labels, autopct="%1.0f%%", colors=colors,
           startangle=140, textprops={"fontsize": 11})


def _graficar_radar(ax, desc, com):
    angulos = np.linspace(0, 2 * np.pi, len(RASGOS), endpoint=False).tolist()
    angulos += angulos[:1]

    hay_datos = False
    for fuente, color, etiqueta in [
        (desc, "#1E88E5", "Descripciones (creador)"),
        (com,  "#E53935", "Comentarios (audiencia)"),
    ]:
        if not fuente:
            continue
        hay_datos = True
        promedios = [np.mean([f["big_five"][r] for f in fuente]) for r in RASGOS]
        promedios += promedios[:1]
        ax.plot(angulos, promedios, "o-", linewidth=2, color=color, label=etiqueta)
        ax.fill(angulos, promedios, alpha=0.15, color=color)

    if not hay_datos:
        ax.text(0, 0, "Sin datos", ha="center", va="center", fontsize=12, color="gray")
        return

    ax.set_thetagrids(np.degrees(angulos[:-1]), [r.capitalize() for r in RASGOS])
    ax.set_ylim(0, 1)
    ax.set_title("Big Five promedio — Creador vs Audiencia", pad=20)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.1), ncol=2, fontsize=10)


def _graficar_emociones(ax, desc, com):
    emociones = list(TRADUCCION_EMOCIONES.keys())
    labels = [TRADUCCION_EMOCIONES[e] for e in emociones]

    def conteo(fuente):
        c = {e: 0 for e in emociones}
        for f in fuente:
            if f.get("emocion") in c:
                c[f["emocion"]] += 1
        return [c[e] for e in emociones]

    x = np.arange(len(labels))
    width = 0.38
    ax.bar(x - width/2, conteo(desc), width, label="Descripciones", color="#1E88E5")
    ax.bar(x + width/2, conteo(com),  width, label="Comentarios",   color="#E53935")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right")
    ax.set_ylabel("Nº de videos")
    ax.set_title("Distribución de emociones", pad=15)
    ax.legend()


def _graficar_heatmap(ax, fuente, titulo):
    ax.set_title(titulo, pad=10)
    if not fuente:
        ax.text(0.5, 0.5, "Sin datos", ha="center", va="center", fontsize=12, color="gray")
        ax.axis("off")
        return
    matriz = np.array([[f["big_five"][r] for f in fuente] for r in RASGOS])
    im = ax.imshow(matriz, cmap="RdYlGn", aspect="auto", vmin=0, vmax=1)
    ax.set_yticks(range(len(RASGOS)))
    ax.set_yticklabels([r.capitalize() for r in RASGOS])
    ax.set_xticks(range(len(fuente)))
    ax.set_xticklabels([f"V{i+1}" for i in range(len(fuente))], fontsize=9)
    plt.colorbar(im, ax=ax, fraction=0.046, pad=0.02)


def main():
    ap = argparse.ArgumentParser(description="Analiza un JSON de Scrappy_Mappy con Groq")
    ap.add_argument("archivo", nargs="?",
                    help="Ruta al JSON (opcional; si se omite, busca el más reciente)")
    ap.add_argument("--max-videos", type=int, default=10)
    args = ap.parse_args()

    ruta = args.archivo or encontrar_json_reciente()
    if not ruta:
        print("❌ No se encontró ningún archivo scrappy_mapy_*.json en esta carpeta.")
        return
    if not Path(ruta).exists():
        print(f"❌ No existe: {ruta}")
        return

    print(f"[archivo] Usando: {ruta}")
    with open(ruta, "r", encoding="utf-8") as f:
        data = json.load(f)

    username = data.get("profile", {}).get("username", "perfil")
    videos = data.get("videos", [])[:args.max_videos]

    tiene_comentarios = any(v.get("top_comments") for v in videos)
    if not tiene_comentarios:
        print("\n⚠️  ADVERTENCIA: Este JSON no contiene comentarios.")
        print("   Los gráficos de la audiencia se mostrarán vacíos.")
        print("   Para solucionarlo, genera un nuevo JSON con:")
        print("   python Scrappy_Mappy.py nasa --with-comments\n")

    print(f"[info] @{username} — {len(videos)} videos a analizar\n")

    resultados = []
    for i, v in enumerate(videos, start=1):
        print(f"[{i}/{len(videos)}] Analizando video...")
        desc_texto = v.get("description") or ""
        coms = v.get("top_comments", []) or []
        com_texto = "\n".join(c.get("text", "") for c in coms if c.get("text"))

        resultados.append({
            "video_url":   v.get("url"),
            "descripcion": analizar_texto(desc_texto, "descripcion"),
            "comentarios": analizar_texto(com_texto, "comentarios"),
        })

    out_json = f"analisis_groq_{username}.json"
    Path(out_json).write_text(
        json.dumps(resultados, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\n[export] {out_json}")

    graficar_todo(resultados, username)


if __name__ == "__main__":
    main()
