"""
tiktok_scraper.py
-----------------
Scraper académico de métricas públicas de TikTok.

Uso:
    python tiktok_scraper.py                              # pregunta el usuario
    python tiktok_scraper.py nasa                         # usuario por CLI
    python tiktok_scraper.py nasa --no-headless           # ver el navegador (depurar)
    python tiktok_scraper.py nasa --output resultados     # nombre base de salida
    python tiktok_scraper.py --batch                      # varios usuarios seguidos
"""

import argparse
import csv
import json
import random
import time
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout


# ============================================================
# 1) PARSER DE NÚMEROS ABREVIADOS ("1.2M" → 1200000)
# ============================================================

MULTIPLIERS = {
    "K": 1_000,
    "M": 1_000_000,
    "B": 1_000_000_000,
    "G": 1_000_000_000,
    "MIL": 1_000,
    "MILL": 1_000_000,
    "MM": 1_000_000,
}


def parse_abbreviated_number(value):
    """Convierte '1.2M', '45.3K', '1,5 M', '1.234' → int. None si no se puede."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)

    s = str(value).strip().upper().replace(" ", "").replace("\u00a0", "")
    if not s:
        return None

    suffix = None
    for suf in ("MILL", "MIL", "MM", "K", "M", "B", "G"):
        if s.endswith(suf):
            suffix = suf
            s = s[: -len(suf)]
            break

    if not s:
        return None

    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        if suffix:
            s = s.replace(",", ".")
        elif s.count(",") == 1 and len(s.split(",")[1]) == 3 and len(s.split(",")[0]) <= 3:
            s = s.replace(",", "")
        else:
            s = s.replace(",", "")
    elif "." in s:
        if suffix:
            pass
        elif s.count(".") > 1:
            s = s.replace(".", "")
        elif len(s.split(".")[1]) == 3 and len(s.split(".")[0]) <= 3:
            s = s.replace(".", "")

    try:
        num = float(s)
    except ValueError:
        return None

    if suffix:
        num *= MULTIPLIERS[suffix]

    return int(round(num))


# ============================================================
# 2) HELPERS DE EXTRACCIÓN
# ============================================================

def _first_text(page, selectors, timeout=3000):
    """Devuelve el primer texto no vacío de la lista de selectores."""
    for sel in selectors:
        try:
            txt = page.locator(sel).first.inner_text(timeout=timeout).strip()
            if txt:
                return txt
        except Exception:
            continue
    return None


def _extract_profile_from_json(html):
    """Busca el perfil en los distintos JSON incrustados que sirve TikTok."""
    soup = BeautifulSoup(html, "html.parser")

    # Intento 1: __UNIVERSAL_DATA_FOR_REHYDRATION__ (estructura actual)
    script = soup.find("script", id="__UNIVERSAL_DATA_FOR_REHYDRATION__")
    if script and script.string:
        try:
            data = json.loads(script.string)
            detail = data.get("__DEFAULT_SCOPE__", {}).get("webapp.user-detail", {})
            user_info = detail.get("userInfo", {})
            if user_info:
                user = user_info.get("user", {})
                stats = user_info.get("stats", {})
                return {
                    "username":    user.get("uniqueId"),
                    "nickname":    user.get("nickname"),
                    "followers":   stats.get("followerCount"),
                    "following":   stats.get("followingCount"),
                    "likes_total": stats.get("heart") or stats.get("heartCount"),
                    "video_count": stats.get("videoCount"),
                }
        except json.JSONDecodeError:
            pass

    # Intento 2: SIGI_STATE (estructura antigua, fallback)
    script = soup.find("script", id="SIGI_STATE")
    if script and script.string:
        try:
            data = json.loads(script.string)
            users = data.get("UserModule", {}).get("users", {})
            stats_map = data.get("UserModule", {}).get("stats", {})
            if users:
                uid = next(iter(users))
                user = users[uid]
                stats = stats_map.get(uid, {})
                return {
                    "username":    user.get("uniqueId"),
                    "nickname":    user.get("nickname"),
                    "followers":   stats.get("followerCount"),
                    "following":   stats.get("followingCount"),
                    "likes_total": stats.get("heartCount") or stats.get("heart"),
                    "video_count": stats.get("videoCount"),
                }
        except (json.JSONDecodeError, StopIteration):
            pass

    return None


def _extract_profile_from_selectors(page):
    """Fallback: leer los contadores directamente del DOM si el JSON falla."""
    return {
        "followers":   _first_text(page, ["strong[data-e2e='followers-count']"]),
        "following":   _first_text(page, ["strong[data-e2e='following-count']"]),
        "likes_total": _first_text(page, ["strong[data-e2e='likes-count']"]),
        "video_count": _first_text(page, ["strong[data-e2e='video-count']"]),
    }


def _dismiss_login_modal(page):
    """
    Cierra el modal de 'Continue as guest' / 'Continuar como invitado' si aparece.
    Devuelve True si lo cerró, False si no había modal.
    """
    selectors = [
        'button:has-text("Continue as guest")',
        'button:has-text("Continuar como invitado")',
        'div[role="dialog"] button:has-text("Continue as guest")',
        'div[role="dialog"] button:has-text("Continuar como invitado")',
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=2000):
                btn.click()
                page.wait_for_timeout(1500)
                print("[login] Modal cerrado como invitado.")
                return True
        except Exception:
            continue
    return False


# ============================================================
# 3) SCRAPER PRINCIPAL
# ============================================================

def scrape_tiktok(username, headless=True, max_videos=10):
    url = f"https://www.tiktok.com/@{username}"
    result = {
        "scraped_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "profile_url": url,
        "profile": None,
        "videos": [],
        "errors": [],
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 768},
            locale="es-ES",
        )
        page = context.new_page()

        # Bloquear imágenes/vídeos/fuentes → más rápido
        page.route(
            "**/*",
            lambda route: route.abort()
            if route.request.resource_type in ("image", "media", "font")
            else route.continue_(),
        )

        # ---------- PERFIL ----------
        # IMPORTANTE: el modal de login se cierra ANTES de esperar el h1.
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)

            # 1) Cerrar modal de login si aparece (esto es lo que provocaba el timeout)
            _dismiss_login_modal(page)

            # 2) Esperar el título del perfil con margen suficiente
            page.wait_for_selector("h1[data-e2e='user-title']", timeout=20_000)
        except PWTimeout:
            msg = f"No se pudo cargar @{username} (timeout o bloqueo tras 20s)"
            print(f"[perfil] {msg}")
            result["errors"].append(msg)
            browser.close()
            return result

        html = page.content()
        profile = _extract_profile_from_json(html)
        if not profile:
            print("[perfil] JSON no encontrado, usando selectores...")
            profile = _extract_profile_from_selectors(page)

        for k in ("followers", "following", "likes_total", "video_count"):
            if k in profile:
                profile[k] = parse_abbreviated_number(profile[k])
        result["profile"] = profile
        print(f"[perfil] @{profile.get('username') or username} "
              f"→ {profile.get('followers')} seguidores")

        # ---------- LISTA DE VIDEOS ----------
        # (el modal ya se cerró arriba; puede volver a aparecer al hacer scroll,
        #  por eso lo reintentamos una vez aquí antes de esperar la cuadrícula)
        _dismiss_login_modal(page)

        try:
            page.wait_for_selector('[data-e2e="user-post-item"]', timeout=20_000)
        except PWTimeout:
            msg = "No se encontraron tarjetas de video (posible login wall o bloqueo)"
            print(f"[videos] {msg}")
            result["errors"].append(msg)
            browser.close()
            return result

        video_urls = []
        for _ in range(10):
            for link in page.locator('[data-e2e="user-post-item"] a[href*="/video/"]').all():
                try:
                    href = link.get_attribute("href")
                except Exception:
                    continue
                if href:
                    if href.startswith("/"):
                        href = "https://www.tiktok.com" + href
                    if href not in video_urls:
                        video_urls.append(href)
            if len(video_urls) >= max_videos:
                break
            page.mouse.wheel(0, 2000)
            page.wait_for_timeout(random.randint(1500, 3000))

        video_urls = video_urls[:max_videos]
        print(f"[videos] Encontrados {len(video_urls)} videos")

        # ---------- DETALLE DE CADA VIDEO (con reintentos) ----------
        for i, vurl in enumerate(video_urls, start=1):
            entry = {"url": vurl, "likes": None, "comments": None, "shares": None}
            for attempt in range(1, 4):
                try:
                    page.goto(vurl, wait_until="domcontentloaded", timeout=30_000)

                    # El modal también puede aparecer en la página del video
                    _dismiss_login_modal(page)

                    page.wait_for_selector("strong[data-e2e='like-count']", timeout=10_000)

                    raw_likes    = _first_text(page, ["strong[data-e2e='like-count']",
                                                      "strong[data-e2e='browse-like-count']"])
                    raw_comments = _first_text(page, ["strong[data-e2e='comment-count']",
                                                      "strong[data-e2e='browse-comment-count']"])
                    raw_shares   = _first_text(page, ["strong[data-e2e='share-count']",
                                                      "strong[data-e2e='browse-share-count']"])

                    entry.update({
                        "likes":         parse_abbreviated_number(raw_likes),
                        "comments":      parse_abbreviated_number(raw_comments),
                        "shares":        parse_abbreviated_number(raw_shares),
                        "likes_raw":     raw_likes,
                        "comments_raw":  raw_comments,
                        "shares_raw":    raw_shares,
                    })
                    print(f"  [{i}/{len(video_urls)}] OK  likes={entry['likes']} "
                          f"comments={entry['comments']} shares={entry['shares']}")
                    break
                except Exception as e:
                    wait = 2 ** attempt + random.uniform(0, 1)
                    print(f"  [{i}/{len(video_urls)}] intento {attempt} falló: {e}. "
                          f"Reintentando en {wait:.1f}s...")
                    page.wait_for_timeout(int(wait * 1000))
            else:
                entry["error"] = "3 intentos fallidos"
                result["errors"].append(f"Video fallido: {vurl}")

            result["videos"].append(entry)
            page.wait_for_timeout(random.randint(2000, 5000))

        browser.close()

    return result


# ============================================================
# 4) EXPORTACIÓN
# ============================================================

def save_json(result, path):
    Path(path).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[export] JSON → {path}")


def save_csv(result, path):
    """Genera un CSV plano: una fila por video, con columnas de perfil repetidas."""
    profile = result.get("profile") or {}
    rows = []
    for v in result.get("videos", []):
        rows.append({
            "scraped_at":     result.get("scraped_at"),
            "username":       profile.get("username"),
            "nickname":       profile.get("nickname"),
            "followers":      profile.get("followers"),
            "following":      profile.get("following"),
            "likes_total":    profile.get("likes_total"),
            "video_count":    profile.get("video_count"),
            "video_url":      v.get("url"),
            "video_likes":    v.get("likes"),
            "video_comments": v.get("comments"),
            "video_shares":   v.get("shares"),
            "error":          v.get("error", ""),
        })

    if not rows:
        rows.append({
            "scraped_at": result.get("scraped_at"),
            "username":   profile.get("username"),
            "nickname":   profile.get("nickname"),
            "followers":  profile.get("followers"),
            "following":  profile.get("following"),
            "likes_total": profile.get("likes_total"),
            "video_count": profile.get("video_count"),
            "video_url": "", "video_likes": "", "video_comments": "",
            "video_shares": "", "error": "",
        })

    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"[export] CSV  → {path}")


# ============================================================
# 5) CLI + MODO INTERACTIVO
# ============================================================

def _run_one(username, headless, max_videos, output):
    """Ejecuta el scraper para un usuario y guarda JSON+CSV."""
    base = output or f"tiktok_{username}_{int(time.time())}"
    print(f"\n=== Scrapeando @{username} (headless={headless}) ===")
    result = scrape_tiktok(username, headless=headless, max_videos=max_videos)
    save_json(result, f"{base}.json")
    save_csv(result, f"{base}.csv")

    if result["errors"]:
        print(f"⚠️  {len(result['errors'])} errores registrados:")
        for e in result["errors"]:
            print(f"   - {e}")
    else:
        n = len(result.get("videos", []))
        print(f"✔ Listo. {n} videos guardados.")
    return result


def main():
    ap = argparse.ArgumentParser(description="Scraper académico de métricas de TikTok")
    ap.add_argument(
        "username",
        nargs="?",
        help="Nombre de usuario (sin @). Si se omite, se pregunta interactivamente.",
    )
    ap.add_argument("--no-headless", action="store_true",
                    help="Muestra el navegador (útil para depurar selectores)")
    ap.add_argument("--output", default=None,
                    help="Nombre base de los archivos de salida (sin extensión)")
    ap.add_argument("--max-videos", type=int, default=10)
    ap.add_argument("--batch", action="store_true",
                    help="Pide varios usuarios seguidos hasta escribir 'salir'")
    args = ap.parse_args()

    headless = not args.no_headless

    # Caso 1: usuario por CLI
    if args.username:
        _run_one(args.username, headless, args.max_videos, args.output)
        return

    # Caso 2: modo interactivo simple
    if not args.batch:
        try:
            username = input("👉 Escribe el usuario de TikTok (sin @): ").strip().lstrip("@")
        except (EOFError, KeyboardInterrupt):
            print("\nCancelado.")
            return
        if not username:
            print("No escribiste ningún usuario. Saliendo.")
            return
        _run_one(username, headless, args.max_videos, args.output)
        return

    # Caso 3: modo batch
    print("Modo batch. Escribe un usuario y Enter. Escribe 'salir' para terminar.")
    while True:
        try:
            username = input("\n👉 Usuario (o 'salir'): ").strip().lstrip("@")
        except (EOFError, KeyboardInterrupt):
            print("\nCancelado.")
            break
        if username.lower() in ("salir", "exit", "quit", ""):
            print("Fin del batch.")
            break
        _run_one(username, headless, args.max_videos, args.output)
        wait = random.randint(15, 30)
        print(f"⏳ Esperando {wait}s antes del siguiente usuario (rate-limit)...")
        time.sleep(wait)


if __name__ == "__main__":
    main()