"""
Scrappy_Mappy.py
-----------------
Scraper académico de métricas públicas de TikTok.

Extrae:
  - Perfil: seguidores, seguidos, likes totales, videos, biografía, verificado, región
  - Videos: likes, comentarios, shares, vistas, descripción, hashtags,
            fecha de subida, duración, música
  - Opcional (--with-comments): texto de los primeros N comentarios por video

Requisitos:
    pip install beautifulsoup4 playwright playwright-stealth yt-dlp curl_cffi
    python -m playwright install chromium

Uso:
    python Scrappy_Mappy.py usuario --no-headless
    python Scrappy_Mappy.py usuario
    python Scrappy_Mappy.py usuario --with-comments
    python Scrappy_Mappy.py --batch
    python Scrappy_Mappy.py usuario --reset-session
"""

import argparse
import csv
import json
import random
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout, Error as PWError

# ============================================================
# 0) SIGILO OPCIONAL
# ============================================================

def _apply_stealth(page):
    try:
        from playwright_stealth import stealth_sync
        stealth_sync(page)
    except ImportError:
        try:
            from playwright_stealth import Stealth
            Stealth().apply_stealth_sync(page)
        except Exception:
            pass

# ============================================================
# 1) PARSER DE NÚMEROS
# ============================================================

MULTIPLIERS = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000, "G": 1_000_000_000,
               "MIL": 1_000, "MILL": 1_000_000, "MM": 1_000_000}
SESSION_FILE = "session.json"

def parse_abbreviated_number(value):
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
# 2) HELPERS DE EXTRACCIÓN (Playwright)
# ============================================================

def _first_text(page, selectors, timeout=3000):
    for sel in selectors:
        try:
            txt = page.locator(sel).first.inner_text(timeout=timeout).strip()
            if txt:
                return txt
        except Exception:
            continue
    return None

def _extract_profile_from_json(html):
    soup = BeautifulSoup(html, "html.parser")
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
                    "bio":         user.get("signature"),
                    "verified":    user.get("verified"),
                    "region":      user.get("region"),
                    "followers":   stats.get("followerCount"),
                    "following":   stats.get("followingCount"),
                    "likes_total": stats.get("heart") or stats.get("heartCount"),
                    "video_count": stats.get("videoCount"),
                }
        except json.JSONDecodeError:
            pass
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
                    "bio":         user.get("signature"),
                    "verified":    user.get("verified"),
                    "region":      user.get("region"),
                    "followers":   stats.get("followerCount"),
                    "following":   stats.get("followingCount"),
                    "likes_total": stats.get("heartCount") or stats.get("heart"),
                    "video_count": stats.get("videoCount"),
                }
        except (json.JSONDecodeError, StopIteration):
            pass
    return None

def _extract_profile_from_selectors(page):
    return {
        "bio":         _first_text(page, ["h2[data-e2e='user-bio']"]),
        "followers":   _first_text(page, ["strong[data-e2e='followers-count']"]),
        "following":   _first_text(page, ["strong[data-e2e='following-count']"]),
        "likes_total": _first_text(page, ["strong[data-e2e='likes-count']"]),
        "video_count": _first_text(page, ["strong[data-e2e='video-count']"]),
    }

def _dismiss_login_modal(page):
    selectors = [
        'button:has-text("Continue as guest")',
        'button:has-text("Continuar como invitado")',
        'div[role="dialog"] button:has-text("Continue as guest")',
        'div[role="dialog"] button:has-text("Continuar como invitado")',
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=1500):
                btn.click()
                page.wait_for_timeout(1500)
                print("[login] Modal cerrado como invitado.")
                return True
        except Exception:
            continue
    return False

def _detect_captcha(page):
    try:
        url = page.url.lower()
        if "captcha" in url or "verify" in url or "/challenge" in url:
            return True
    except Exception:
        pass
    try:
        for iframe in page.locator("iframe").all():
            try:
                src = (iframe.get_attribute("src") or "").lower()
                if "captcha" in src or "verify" in src:
                    return True
            except Exception:
                continue
    except Exception:
        pass
    text_or_sel = [
        "#captcha-verify-container", "[class*='captcha']",
        "text=/captcha/i", "text=/verifica que eres humano/i",
        "text=/verify you are human/i", "text=/gira la imagen/i",
        "text=/rotate the image/i", "text=/arrastra el slider/i",
        "text=/drag the slider/i",
    ]
    for sel in text_or_sel:
        try:
            if page.locator(sel).first.is_visible(timeout=500):
                return True
        except Exception:
            continue
    return False

def _handle_captcha_if_present(page, headless, max_wait=300):
    if not _detect_captcha(page):
        return True
    if headless:
        print("\n⚠️  CAPTCHA detectado en modo headless. Usa --no-headless la 1ª vez.")
        return False
    print("\n" + "=" * 62)
    print("⚠️  CAPTCHA DETECTADO — Resuélvelo en la ventana del navegador")
    print(f"   Tienes {max_wait}s. El script continuará solo al resolverlo.")
    print("=" * 62)
    deadline = time.time() + max_wait
    while time.time() < deadline:
        if not _detect_captcha(page):
            page.wait_for_timeout(2000)
            if not _detect_captcha(page):
                print("[captcha] Resuelto. Continuando...\n")
                return True
        page.wait_for_timeout(2000)
    print(f"[captcha] No se resolvió en {max_wait}s. Abortando.")
    return False

def _save_session(context, label=""):
    try:
        context.storage_state(path=SESSION_FILE)
        suffix = f" ({label})" if label else ""
        print(f"[session] Estado guardado en {SESSION_FILE}{suffix}")
    except Exception as e:
        print(f"[session] No se pudo guardar el estado: {e}")

# ============================================================
# 3) EXTRACCIÓN DE MÉTRICAS DE VIDEO CON YT-DLP
# ============================================================

def _extract_hashtags(data):
    """Hashtags del video, desde varias fuentes de yt-dlp."""
    tags = data.get("hashtags")
    if tags:
        return list(tags)
    challenges = data.get("challenges")
    if challenges:
        return [c.get("title", "") for c in challenges
                if isinstance(c, dict) and c.get("title")]
    desc = data.get("description") or ""
    return re.findall(r"#(\w+)", desc)

def _extract_music(data):
    """Música del video, en formato 'titulo - autor' si es posible."""
    music = data.get("music_info")
    if isinstance(music, dict):
        title = music.get("title")
        author = music.get("author")
        if title and author:
            return f"{title} - {author}"
        return title or author
    return data.get("track")

def get_video_metrics_ytdlp(video_url):
    """
    Usa yt-dlp para obtener métricas y metadatos del video.
    Devuelve un dict o None si falla.
    """
    cmd = [
        "yt-dlp",
        "--impersonate=chrome-131",
        "--dump-json",
        "--skip-download",
        "--no-warnings",
        video_url,
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
        )
        if proc.returncode != 0:
            err = (proc.stderr or "")[:300]
            print(f"    [yt-dlp] Error en {video_url}: {err.strip()}")
            return None
        data = json.loads(proc.stdout)
        return {
            "likes":       data.get("like_count"),
            "comments":    data.get("comment_count"),
            "shares":      data.get("repost_count"),
            "views":       data.get("view_count"),
            "description": data.get("description"),
            "hashtags":    _extract_hashtags(data),
            "upload_date": data.get("upload_date"),
            "duration":    data.get("duration"),
            "music":       _extract_music(data),
        }
    except subprocess.TimeoutExpired:
        print(f"    [yt-dlp] Timeout para {video_url}")
        return None
    except (json.JSONDecodeError, Exception) as e:
        print(f"    [yt-dlp] Fallo inesperado para {video_url}: {e}")
        return None

# ============================================================
# 4) COMENTARIOS VÍA MODAL DE PLAYWRIGHT (opcional)
# ============================================================

def get_video_comments_playwright(page, video_id, max_comments=10):
    """
    Desde la página del perfil, abre el video en modal y extrae comentarios.
    Usa un truco de JS para evitar la navegación por defecto del enlace.
    """
    try:
        # 1. Encontrar la tarjeta y hacer scroll
        link = page.locator(f'a[href*="/video/{video_id}"]').first
        link.scroll_into_view_if_needed(timeout=5000)
        page.wait_for_timeout(1000)

        # 2. TRUCO: Quitar el href temporalmente y hacer clic vía JS
        # Esto evita que el navegador navegue a la URL del video.
        link.evaluate("""
            el => {
                el.setAttribute('data-old-href', el.getAttribute('href'));
                el.removeAttribute('href');
                el.click();
            }
        """)
        page.wait_for_timeout(3500) # Esperar a que el modal cargue

        # 3. Restaurar el href por si acaso (aunque el modal esté abierto)
        try:
            page.evaluate("""
                () => {
                    document.querySelectorAll('a[data-old-href]').forEach(el => {
                        el.setAttribute('href', el.getAttribute('data-old-href'));
                        el.removeAttribute('data-old-href');
                    });
                }
            """)
        except Exception:
            pass

        # 4. Verificar si abrió el modal o si navegó
        if f"/video/{video_id}" in page.url:
            print(f"    [comments] Aún navegó a la página. Regresando...")
            page.go_back()
            page.wait_for_timeout(2000)
            page.keyboard.press("Escape")
            page.wait_for_timeout(1000)
            return []

        # 5. Esperar a que carguen los comentarios en el modal
        selectors = [
            '[data-e2e="comment-level-1"]',
            'div[class*="DivCommentItemContainer"]',
            'p[data-e2e="comment-level-1"]'
        ]
        
        loaded = False
        for sel in selectors:
            try:
                page.wait_for_selector(sel, timeout=7000)
                loaded = True
                break
            except PWTimeout:
                continue

        if not loaded:
            print(f"    [comments] Modal abierto, pero sin comentarios para {video_id}")
            page.keyboard.press("Escape")
            page.wait_for_timeout(1500)
            return []

        # 6. Extraer comentarios
        comments = []
        seen = set()
        
        for _ in range(5):
            elements = page.locator('[data-e2e="comment-level-1"]').all()
            if not elements:
                elements = page.locator('div[class*="DivCommentItemContainer"]').all()
                
            for el in elements:
                try:
                    text_el = el.locator("p").first
                    if text_el.count() > 0:
                        text = text_el.inner_text(timeout=2000).strip()
                    else:
                        text = el.inner_text(timeout=2000).strip()
                        
                    if not text or text in seen:
                        continue
                    
                    likes = None
                    try:
                        likes_el = el.locator('[data-e2e="comment-like-count"]').first
                        if likes_el.count() > 0:
                            likes = likes_el.inner_text().strip()
                    except Exception:
                        pass
                        
                    comments.append({"text": text, "likes": likes})
                    seen.add(text)
                except Exception:
                    continue
            
            if len(comments) >= max_comments:
                break
                
            # 7. Hacer scroll dentro del panel de comentarios
            try:
                page.locator('[data-e2e="comment-list"]').first.evaluate(
                    "el => el.scrollTop = el.scrollHeight"
                )
            except Exception:
                page.mouse.wheel(0, 800)
            page.wait_for_timeout(1500)

        # 8. Cerrar el modal
        page.keyboard.press("Escape")
        page.wait_for_timeout(2500)  # Pausa clave para que el modal desaparezca del DOM
        
        return comments[:max_comments]
        
    except Exception as e:
        print(f"    [comments] Error inesperado en {video_id}: {e}")
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(1000)
        except Exception:
            pass
        return []
# ============================================================
# 5) SCRAPER PRINCIPAL
# ============================================================

def scrape_tiktok(username, headless=True, max_videos=20,
                  reset_session=False, with_comments=False,
                  max_comments=10):
    url = f"https://www.tiktok.com/@{username}"
    result = {
        "scraped_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "profile_url": url,
        "profile": None,
        "videos": [],
        "errors": [],
    }

    session_path = Path(SESSION_FILE)
    if reset_session and session_path.exists():
        session_path.unlink()
        print("[session] Sesión anterior eliminada (--reset-session).")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = None
        try:
            ctx_kwargs = dict(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1366, "height": 768},
                locale="es-ES",
            )
            if session_path.exists():
                ctx_kwargs["storage_state"] = str(session_path)
                print(f"[session] Cargando sesión guardada desde {SESSION_FILE}")
            else:
                print(f"[session] Sin sesión previa. Se creará {SESSION_FILE}.")

            context = browser.new_context(**ctx_kwargs)
            page = context.new_page()
            _apply_stealth(page)

            page.route(
                "**/*",
                lambda route: route.abort()
                if route.request.resource_type in ("image", "media", "font")
                else route.continue_(),
            )

            # ---------- PERFIL ----------
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                page.wait_for_timeout(2000)

                if not _handle_captcha_if_present(page, headless):
                    msg = f"CAPTCHA no resuelto para @{username}"
                    print(f"[perfil] {msg}")
                    result["errors"].append(msg)
                    return result

                _dismiss_login_modal(page)
                page.wait_for_selector("h1[data-e2e='user-title']", timeout=20_000)

            except PWTimeout:
                msg = f"No se pudo cargar @{username} (timeout o bloqueo)"
                print(f"[perfil] {msg}")
                result["errors"].append(msg)
                return result
            except PWError as e:
                msg = f"Error de navegador al cargar @{username}: {e}"
                print(f"[perfil] {msg}")
                result["errors"].append(msg)
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
            bio_preview = (profile.get("bio") or "")[:60]
            print(f"[perfil] @{profile.get('username') or username} "
                  f"→ {profile.get('followers')} seguidores")
            if bio_preview:
                print(f"[perfil] bio: {bio_preview}...")

            _save_session(context, label="tras cargar perfil")

                   # ---------- LISTA DE VIDEOS ----------
            _dismiss_login_modal(page)
            
            # Pequeño scroll para forzar la carga de la cuadrícula
            page.wait_for_timeout(2000)
            page.mouse.wheel(0, 600)
            page.wait_for_timeout(2000)

            try:
                page.wait_for_selector('[data-e2e="user-post-item"]', timeout=20_000)
            except PWTimeout:
                msg = "No se encontraron tarjetas de video (posible login wall o bloqueo)"
                print(f"[videos] {msg}")
                result["errors"].append(msg)
                try:
                    page.screenshot(path="debug_videos_fallo.png")
                    print("[debug] Captura guardada en debug_videos_fallo.png.")
                except Exception:
                    pass
                if not headless:
                    print("\nPresiona ENTER en esta ventana para cerrar el navegador...")
                    input()
                return result

            video_ids = []
            for _ in range(20):
                for link in page.locator('[data-e2e="user-post-item"] a[href*="/video/"]').all():
                    try:
                        href = link.get_attribute("href") or ""
                    except Exception:
                        continue
                    m = re.search(r"/video/(\d+)", href)
                    if m:
                        vid = m.group(1)
                        if vid not in video_ids:
                            video_ids.append(vid)
                if len(video_ids) >= max_videos:
                    break
                page.mouse.wheel(0, 2000)
                page.wait_for_timeout(random.randint(1500, 3000))

            video_ids = video_ids[:max_videos]
            video_urls = [f"https://www.tiktok.com/@{username}/video/{vid}" for vid in video_ids]
            print(f"[videos] Encontrados {len(video_urls)} videos. Extrayendo métricas con yt-dlp...")

            
            # ---------- MÉTRICAS CON YT-DLP ----------
            for i, vurl in enumerate(video_urls, start=1):
                entry = {"url": vurl, "likes": None, "comments": None, "shares": None}
                metrics = get_video_metrics_ytdlp(vurl)
                if metrics:
                    entry.update(metrics)
                    print(f"  [{i}/{len(video_urls)}] OK  likes={metrics['likes']} "
                          f"comments={metrics['comments']} shares={metrics['shares']}")
                else:
                    entry["error"] = "yt-dlp no pudo extraer métricas"
                    result["errors"].append(f"Video fallido (yt-dlp): {vurl}")
                    print(f"  [{i}/{len(video_urls)}] FALLO  {vurl}")

                result["videos"].append(entry)
                time.sleep(random.uniform(1.5, 3.0))

            # ---------- COMENTARIOS VÍA MODAL (opcional) ----------
                       # ---------- COMENTARIOS (opcional) ----------
            if with_comments and video_urls:
                print(f"\n[comments] Extrayendo primeros {max_comments} comentarios por video...")
                for i, vurl in enumerate(video_urls, start=1):
                    comments = get_video_comments_playwright(
                        page, vurl, max_comments=max_comments
                    )
                    for entry in result["videos"]:
                        if entry["url"] == vurl:
                            entry["top_comments"] = comments
                            break
                    print(f"  [comments {i}/{len(video_urls)}] {len(comments)} comentarios")
                    time.sleep(random.uniform(2.0, 4.0))

            return result

        finally:
            try:
                browser.close()
            except Exception:
                pass

# ============================================================
# 6) EXPORTACIÓN
# ============================================================

def save_json(result, path):
    Path(path).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[export] JSON → {path}")

def save_csv(result, path):
    profile = result.get("profile") or {}
    rows = []
    for v in result.get("videos", []):
        hashtags = v.get("hashtags") or []
        hashtags_str = ",".join(hashtags) if isinstance(hashtags, list) else str(hashtags)
        rows.append({
            "scraped_at":        result.get("scraped_at"),
            "username":          profile.get("username"),
            "nickname":          profile.get("nickname"),
            "bio":               profile.get("bio"),
            "verified":          profile.get("verified"),
            "region":            profile.get("region"),
            "followers":         profile.get("followers"),
            "following":         profile.get("following"),
            "likes_total":       profile.get("likes_total"),
            "video_count":       profile.get("video_count"),
            "video_url":         v.get("url"),
            "video_likes":       v.get("likes"),
            "video_comments":    v.get("comments"),
            "video_shares":      v.get("shares"),
            "video_views":       v.get("views"),
            "video_description": v.get("description"),
            "video_hashtags":    hashtags_str,
            "video_upload_date": v.get("upload_date"),
            "video_duration_s":  v.get("duration"),
            "video_music":       v.get("music"),
            "error":             v.get("error", ""),
        })
    if not rows:
        rows.append({
            "scraped_at": result.get("scraped_at"),
            "username":   profile.get("username"),
            "nickname":   profile.get("nickname"),
            "bio":        profile.get("bio"),
            "verified":   profile.get("verified"),
            "region":     profile.get("region"),
            "followers":  profile.get("followers"),
            "following":  profile.get("following"),
            "likes_total": profile.get("likes_total"),
            "video_count": profile.get("video_count"),
            "video_url": "", "video_likes": "", "video_comments": "",
            "video_shares": "", "video_views": "", "video_description": "",
            "video_hashtags": "", "video_upload_date": "",
            "video_duration_s": "", "video_music": "", "error": "",
        })
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"[export] CSV  → {path}")

def save_comments_csv(result, path):
    """Una fila por comentario (solo si se usó --with-comments)."""
    rows = []
    for v in result.get("videos", []):
        for c in v.get("top_comments", []) or []:
            rows.append({
                "video_url":     v.get("url"),
                "comment_text":  c.get("text"),
                "comment_likes": c.get("likes"),
            })
    if not rows:
        print("[export] Sin comentarios que exportar (usa --with-comments).")
        return
    fieldnames = ["video_url", "comment_text", "comment_likes"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"[export] CSV comentarios → {path} ({len(rows)} filas)")

# ============================================================
# 7) CLI
# ============================================================

def _run_one(username, headless, max_videos, output, reset_session,
             with_comments, max_comments):
    base = output or f"scrappy_mapy_{username}_{int(time.time())}"
    print(f"\n=== Scrapeando @{username} (headless={headless}, with_comments={with_comments}) ===")
    result = scrape_tiktok(
        username,
        headless=headless,
        max_videos=max_videos,
        reset_session=reset_session,
        with_comments=with_comments,
        max_comments=max_comments,
    )
    save_json(result, f"{base}.json")
    save_csv(result, f"{base}.csv")
    save_comments_csv(result, f"{base}_comments.csv")

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
    ap.add_argument("username", nargs="?", help="Nombre de usuario (sin @). Si se omite, se pregunta.")
    ap.add_argument("--no-headless", action="store_true", help="Muestra el navegador (1ª vez para captcha)")
    ap.add_argument("--output", default=None, help="Nombre base de los archivos de salida")
    ap.add_argument("--max-videos", type=int, default=10)
    ap.add_argument("--with-comments", action="store_true",
                    help="Extrae también el texto de los comentarios (más lento)")
    ap.add_argument("--max-comments", type=int, default=10,
                    help="Comentarios por video (solo con --with-comments)")
    ap.add_argument("--batch", action="store_true", help="Pide varios usuarios seguidos")
    ap.add_argument("--reset-session", action="store_true", help="Borra session.json")
    args = ap.parse_args()

    headless = not args.no_headless
    reset = args.reset_session

    if args.username:
        _run_one(args.username, headless, args.max_videos, args.output, reset,
                 args.with_comments, args.max_comments)
        return

    if not args.batch:
        try:
            username = input("👉 Escribe el usuario de TikTok (sin @): ").strip().lstrip("@")
        except (EOFError, KeyboardInterrupt):
            print("\nCancelado.")
            return
        if not username:
            print("No escribiste ningún usuario. Saliendo.")
            return
        _run_one(username, headless, args.max_videos, args.output, reset,
                 args.with_comments, args.max_comments)
        return

    print("Modo batch. Escribe un usuario y Enter. Escribe 'salir' para terminar.")
    first = True
    while True:
        try:
            username = input("\n👉 Usuario (o 'salir'): ").strip().lstrip("@")
        except (EOFError, KeyboardInterrupt):
            print("\nCancelado.")
            break
        if username.lower() in ("salir", "exit", "quit", ""):
            print("Fin del batch.")
            break
        _run_one(username, headless, args.max_videos, args.output,
                 reset if first else False,
                 args.with_comments, args.max_comments)
        first = False
        wait = random.randint(15, 30)
        print(f"⏳ Esperando {wait}s antes del siguiente usuario...")
        time.sleep(wait)

if __name__ == "__main__":
    main()