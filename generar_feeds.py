#!/usr/bin/env python3
"""
Fábrica de feeds RSS gratuitos.

Para cada fuente definida en fuentes.json intenta, en este orden:

  1. El feed nativo de WordPress   ->  <url_categoria>feed/
  2. La API REST de WordPress      ->  /wp-json/wp/v2/posts?categories=<id>
  3. Raspado del HTML de la página de categoría (último recurso)

y escribe un archivo RSS 2.0 válido en docs/, que se publica con GitHub Pages.
Los repos de podcast solo tienen que apuntar su feeds.txt a esa URL.

Regla de seguridad: si una fuente falla, NO se sobrescribe el XML anterior.
Es preferible un feed viejo a un feed vacío (que borraría episodios del ciclo).
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from urllib.parse import urlparse, urljoin
from xml.sax.saxutils import escape

import requests
from bs4 import BeautifulSoup

RAIZ = Path(__file__).resolve().parent
SALIDA = RAIZ / "docs"
CONFIG = RAIZ / "fuentes.json"

# Sin la palabra "Mozilla": así se evita el reto anti-bot Anubis, que por
# defecto solo desafía a los agentes que se anuncian como navegador.
USER_AGENT = "rebpodcast-feedbot/1.0 (+https://github.com/robertoroseteg-cloud)"

TIMEOUT = 30
MAX_ITEMS = 25
REINTENTOS = 3

DIAS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MESES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
         "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# Primeros segmentos de ruta que NUNCA son artículos
NO_ARTICULOS = {
    "categoria", "categorias", "autor", "autores", "tag", "etiqueta",
    "wp-content", "wp-json", "wp-admin", "wp-includes", "download",
    "galeria", "rebelion", "feed", "page", "nosotros", "terminos-uso",
    "libros-libres", "busqueda-avanzada", "buscar", "contacto", "donar",
}

sesion = requests.Session()
sesion.headers.update({
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml,*/*",
    "Accept-Language": "es-ES,es;q=0.9",
})


# --------------------------------------------------------------------------
# utilidades
# --------------------------------------------------------------------------

def log(msg: str) -> None:
    print(msg, flush=True)


def pedir(url: str, **kw):
    """GET con reintentos y espaldarazo exponencial. Devuelve Response o None."""
    for intento in range(1, REINTENTOS + 1):
        try:
            r = sesion.get(url, timeout=TIMEOUT, **kw)
            if r.status_code == 200:
                return r
            if r.status_code in (429, 500, 502, 503, 504) and intento < REINTENTOS:
                time.sleep(3 * intento)
                continue
            return None
        except requests.RequestException:
            if intento < REINTENTOS:
                time.sleep(3 * intento)
                continue
            return None
    return None


def rfc822(dt: datetime) -> str:
    """Fecha en formato RFC-822, sin depender del locale del sistema."""
    dt = dt.astimezone(timezone.utc)
    return "%s, %02d %s %04d %02d:%02d:%02d +0000" % (
        DIAS[dt.weekday()], dt.day, MESES[dt.month - 1],
        dt.year, dt.hour, dt.minute, dt.second,
    )


def limpiar(texto: str, limite: int = 400) -> str:
    texto = re.sub(r"<[^>]+>", " ", texto or "")
    texto = unescape(texto)            # &laquo; &oacute; &#8220; ...
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto[:limite]


def parsear_fecha_es(texto: str) -> datetime | None:
    """Extrae dd/mm/aaaa de un bloque de texto."""
    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", texto or "")
    if not m:
        return None
    d, mes, a = (int(x) for x in m.groups())
    try:
        return datetime(a, mes, d, 12, 0, 0, tzinfo=timezone.utc)
    except ValueError:
        return None


# --------------------------------------------------------------------------
# estrategia 1: feed nativo de WordPress
# --------------------------------------------------------------------------

def feed_nativo(url_categoria: str) -> str | None:
    url = urljoin(url_categoria if url_categoria.endswith("/") else url_categoria + "/", "feed/")
    r = pedir(url)
    if not r:
        return None
    cabeza = r.text[:600].lower()
    if "<rss" in cabeza or "<feed" in cabeza or "<rdf" in cabeza:
        if "<item" in r.text.lower() or "<entry" in r.text.lower():
            log(f"    feed nativo OK  -> {url}")
            return r.text
    return None


# --------------------------------------------------------------------------
# estrategia 2: API REST de WordPress
# --------------------------------------------------------------------------

def api_rest(url_categoria: str) -> list[dict]:
    partes = urlparse(url_categoria)
    base = f"{partes.scheme}://{partes.netloc}"
    slug = [s for s in partes.path.split("/") if s][-1]

    r = pedir(f"{base}/wp-json/wp/v2/categories",
              params={"slug": slug, "_fields": "id,slug,name"})
    if not r:
        return []
    try:
        cats = r.json()
    except ValueError:
        return []
    if not isinstance(cats, list) or not cats:
        return []
    cat_id = cats[0].get("id")

    r = pedir(f"{base}/wp-json/wp/v2/posts", params={
        "categories": cat_id,
        "per_page": MAX_ITEMS,
        "orderby": "date",
        "order": "desc",
        "_fields": "link,date_gmt,title,excerpt",
    })
    if not r:
        return []
    try:
        posts = r.json()
    except ValueError:
        return []

    items = []
    for p in posts if isinstance(posts, list) else []:
        enlace = p.get("link")
        titulo = limpiar((p.get("title") or {}).get("rendered", ""), 300)
        if not enlace or not titulo:
            continue
        try:
            fecha = datetime.fromisoformat(p["date_gmt"]).replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            fecha = datetime.now(timezone.utc)
        items.append({
            "titulo": titulo,
            "enlace": enlace,
            "fecha": fecha,
            "resumen": limpiar((p.get("excerpt") or {}).get("rendered", "")),
        })
    if items:
        log(f"    API REST OK     -> {len(items)} entradas (categoría {cat_id})")
    return items


# --------------------------------------------------------------------------
# estrategia 3: raspado del HTML
# --------------------------------------------------------------------------

def es_articulo(href: str, dominio: str) -> bool:
    p = urlparse(href)
    if p.netloc and p.netloc.replace("www.", "") != dominio.replace("www.", ""):
        return False
    segmentos = [s for s in p.path.split("/") if s]
    if len(segmentos) != 1:
        return False
    if segmentos[0] in NO_ARTICULOS:
        return False
    if p.query or p.fragment:
        return False
    return True


def raspar_html(url_categoria: str, selector: str | None = None) -> list[dict]:
    r = pedir(url_categoria)
    if not r:
        return []
    sopa = BeautifulSoup(r.text, "lxml")
    dominio = urlparse(url_categoria).netloc

    contenedores = []
    for sel in ([selector] if selector else ["main article", "#content article", "article", ".post"]):
        if not sel:
            continue
        encontrados = sopa.select(sel)
        if len(encontrados) >= 3:
            contenedores = encontrados
            break

    vistos, items = set(), []

    def agregar(enlace_tag, ambito):
        href = urljoin(url_categoria, enlace_tag.get("href", "").split("#")[0])
        if not es_articulo(href, dominio) or href in vistos:
            return
        titulo = limpiar(enlace_tag.get_text(" "), 300)
        if len(titulo) < 15:
            return
        vistos.add(href)
        fecha = parsear_fecha_es(ambito.get_text(" ") if ambito else "")
        items.append({
            "titulo": titulo,
            "enlace": href,
            "fecha": fecha or datetime.now(timezone.utc),
            "resumen": "",
        })

    if contenedores:
        for c in contenedores:
            for a in c.select("h1 a[href], h2 a[href], h3 a[href]") or c.select("a[href]"):
                agregar(a, c)
    else:
        # respaldo: barrer toda la página priorizando titulares
        for a in sopa.select("h1 a[href], h2 a[href], h3 a[href]"):
            padre = a.find_parent(["article", "div", "li"]) or a.parent
            agregar(a, padre)

    if items:
        log(f"    raspado HTML OK -> {len(items)} entradas")
    return items[:MAX_ITEMS]


# --------------------------------------------------------------------------
# construcción del RSS
# --------------------------------------------------------------------------

def construir_rss(nombre: str, enlace: str, items: list[dict]) -> str:
    items = sorted(items, key=lambda i: i["fecha"], reverse=True)[:MAX_ITEMS]
    ahora = rfc822(datetime.now(timezone.utc))

    partes = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
        "  <channel>",
        f"    <title>{escape(nombre)}</title>",
        f"    <link>{escape(enlace)}</link>",
        f"    <description>{escape(nombre)} — feed generado automáticamente</description>",
        "    <language>es</language>",
        f"    <lastBuildDate>{ahora}</lastBuildDate>",
        "    <generator>fabrica-de-feeds</generator>",
    ]
    for it in items:
        partes += [
            "    <item>",
            f"      <title>{escape(it['titulo'])}</title>",
            f"      <link>{escape(it['enlace'])}</link>",
            f"      <guid isPermaLink=\"true\">{escape(it['enlace'])}</guid>",
            f"      <pubDate>{rfc822(it['fecha'])}</pubDate>",
            f"      <description>{escape(it['resumen'] or it['titulo'])}</description>",
            "    </item>",
        ]
    partes += ["  </channel>", "</rss>", ""]
    return "\n".join(partes)


def contar_items(xml: str) -> int:
    return len(re.findall(r"<item[\s>]", xml)) or len(re.findall(r"<entry[\s>]", xml))


# --------------------------------------------------------------------------
# principal
# --------------------------------------------------------------------------

def procesar(fuente: dict) -> dict:
    nombre = fuente["nombre"]
    url = fuente["url"]
    destino = SALIDA / fuente["salida"]
    log(f"\n>> {nombre}\n   {url}")

    xml, metodo = None, None

    if fuente.get("intentar_nativo", True):
        crudo = feed_nativo(url)
        if crudo and contar_items(crudo) > 0:
            xml, metodo = crudo, "feed nativo"

    if xml is None:
        items = api_rest(url)
        if items:
            xml, metodo = construir_rss(nombre, url, items), "api rest"

    if xml is None:
        items = raspar_html(url, fuente.get("selector"))
        if items:
            xml, metodo = construir_rss(nombre, url, items), "raspado html"

    if xml is None or contar_items(xml) == 0:
        if destino.exists():
            log(f"    !! FALLO: se conserva el archivo anterior ({destino.name})")
            return {"salida": fuente["salida"], "ok": False, "metodo": None,
                    "items": 0, "nota": "fallo, se conservó el XML previo"}
        log(f"    !! FALLO: no se pudo generar {destino.name}")
        return {"salida": fuente["salida"], "ok": False, "metodo": None,
                "items": 0, "nota": "fallo, sin archivo previo"}

    destino.write_text(xml, encoding="utf-8")
    n = contar_items(xml)
    log(f"    escrito {destino.name} ({n} entradas, vía {metodo})")
    return {"salida": fuente["salida"], "ok": True, "metodo": metodo,
            "items": n, "nota": ""}


def escribir_indice(fuentes: list[dict], resultados: list[dict]) -> None:
    filas = []
    por_salida = {r["salida"]: r for r in resultados}
    for f in fuentes:
        r = por_salida.get(f["salida"], {})
        estado = "OK" if r.get("ok") else "FALLO"
        filas.append(
            f"<tr><td><a href='{f['salida']}'>{f['salida']}</a></td>"
            f"<td>{f['nombre']}</td>"
            f"<td>{', '.join(f.get('repos', []))}</td>"
            f"<td>{r.get('items', 0)}</td>"
            f"<td>{r.get('metodo') or '—'}</td>"
            f"<td>{estado}</td></tr>"
        )
    html_doc = f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<title>Fábrica de feeds</title>
<style>
 body{{font:15px/1.5 system-ui,sans-serif;max-width:60rem;margin:3rem auto;padding:0 1rem}}
 table{{border-collapse:collapse;width:100%}} th,td{{border-bottom:1px solid #ddd;padding:.5rem;text-align:left}}
 code{{background:#f4f4f4;padding:.1rem .3rem}}
</style></head><body>
<h1>Fábrica de feeds</h1>
<p>Última actualización: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</p>
<table><tr><th>Archivo</th><th>Fuente</th><th>Repos</th><th>Entradas</th><th>Método</th><th>Estado</th></tr>
{chr(10).join(filas)}
</table></body></html>
"""
    (SALIDA / "index.html").write_text(html_doc, encoding="utf-8")
    (SALIDA / "estado.json").write_text(
        json.dumps({"generado": datetime.now(timezone.utc).isoformat(),
                    "resultados": resultados}, ensure_ascii=False, indent=2),
        encoding="utf-8")


def main() -> int:
    SALIDA.mkdir(parents=True, exist_ok=True)
    fuentes = json.loads(CONFIG.read_text(encoding="utf-8"))["fuentes"]

    solo = sys.argv[1] if len(sys.argv) > 1 else None
    if solo:
        fuentes = [f for f in fuentes if solo in (f["salida"], f["nombre"])]
        if not fuentes:
            log(f"No hay ninguna fuente que coincida con «{solo}»")
            return 2

    resultados = [procesar(f) for f in fuentes]
    escribir_indice(fuentes, resultados)

    fallos = [r for r in resultados if not r["ok"]]
    log("\n" + "=" * 60)
    log(f"{len(resultados) - len(fallos)}/{len(resultados)} feeds generados")
    for r in fallos:
        log(f"  FALLO: {r['salida']} ({r['nota']})")
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
