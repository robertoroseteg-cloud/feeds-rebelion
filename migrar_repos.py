#!/usr/bin/env python3
"""
Migra los repos de podcast de PolitePaul a la fábrica de feeds propia.

Por cada repo:
  1. sustituye la URL de politepaul.com en feeds.txt por la de GitHub Pages
  2. si el cron sigue en "0 12 * * *", le pone su minuto escalonado

Por defecto NO escribe nada: clona, calcula el diff y te lo enseña.
Solo con --aplicar hace commit y push.

Requisitos: git y gh (GitHub CLI) autenticado -> gh auth status

    python3 migrar_repos.py                  # ver los diffs
    python3 migrar_repos.py --solo rebecuador
    python3 migrar_repos.py --solo rebecuador --aplicar
    python3 migrar_repos.py --aplicar        # los diecisiete
"""

from __future__ import annotations

import argparse
import difflib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

USUARIO = "robertoroseteg-cloud"
BASE_FEEDS = f"https://{USUARIO}.github.io/feeds-rebelion"

# repo -> (archivo xml en la fábrica, minuto de cron)
# Los minutos 39/41/43/45 son nuevos: los cuatro repos que seguían sin escalonar.
REPOS: dict[str, tuple[str, int]] = {
    "rebecuador":                ("rebecuador.xml",     5),
    "reb-ecuador":               ("rebecuador.xml",    41),
    "rebvenezuela":              ("rebvenezuela.xml",   7),
    "rebmedioslib":              ("rebmedioslib.xml",   9),
    "rebfeminismo":              ("rebfeminismo.xml",  11),
    "rebeconomia":               ("rebeconomia.xml",   13),
    "rebecosoc":                 ("rebecosoc.xml",     15),
    "rebcultura":                ("rebcultura.xml",    17),
    "rebconlib":                 ("rebconlib.xml",     19),
    "rebAL":                     ("rebAL.xml",         21),
    "rebcolombia":               ("rebcolombia.xml",   23),
    "rebcuba":                   ("rebcuba.xml",       27),
    "reb-cuba":                  ("rebcuba.xml",       43),
    "reb-otro-mundo-es-posible": ("rebotromundo.xml",  29),
    "reb-mexico":                ("rebmexico.xml",     39),
    "rebargentina":              ("rebargentina.xml",  45),
    # "desinfoopinion" queda FUERA a proposito: Anubis bloquea desinformemonos.org
    # y su XML no se genera. Migrarlo lo dejaria apuntando a un 404.
    # "desinfoopinion":          ("desinfoopinion.xml", 33),
}

CRON_SIN_ESCALONAR = re.compile(r"(cron:\s*[\"']?)0(\s+12\s+\*\s+\*\s+\*)")


# --------------------------------------------------------------------------
# lógica pura (fácil de probar sin red)
# --------------------------------------------------------------------------

def nuevo_feeds_txt(texto: str, url_nueva: str) -> tuple[str, str]:
    """Devuelve (texto_nuevo, motivo). Si no hay nada que hacer, motivo lo dice."""
    if url_nueva in texto:
        return texto, "ya apuntaba a la fábrica"

    lineas = texto.splitlines()
    sustituidas = 0
    for i, linea in enumerate(lineas):
        if "politepaul.com" in linea:
            lineas[i] = url_nueva
            sustituidas += 1

    if sustituidas == 0:
        return texto, "sin línea de politepaul: revísalo a mano"
    if sustituidas > 1:
        return texto, f"¡{sustituidas} líneas de politepaul! revísalo a mano"

    salida = "\n".join(lineas)
    if texto.endswith("\n"):
        salida += "\n"
    return salida, "ok"


def nuevo_cron(texto: str, minuto: int) -> tuple[str, str]:
    nuevo, n = CRON_SIN_ESCALONAR.subn(rf"\g<1>{minuto}\g<2>", texto)
    if n == 0:
        return texto, "cron ya escalonado"
    return nuevo, f"cron 0 -> {minuto}"


def diff(antes: str, despues: str, nombre: str) -> str:
    return "".join(difflib.unified_diff(
        antes.splitlines(keepends=True), despues.splitlines(keepends=True),
        fromfile=f"a/{nombre}", tofile=f"b/{nombre}"))


# --------------------------------------------------------------------------
# plomería git
# --------------------------------------------------------------------------

def correr(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def procesar(repo: str, xml: str, minuto: int, aplicar: bool, tmp: Path) -> str:
    destino = tmp / repo
    r = correr(["gh", "repo", "clone", f"{USUARIO}/{repo}", str(destino), "--", "--depth", "1"])
    if r.returncode != 0:
        return f"ERROR al clonar: {r.stderr.strip().splitlines()[-1] if r.stderr else ''}"

    notas, cambios = [], []

    # --- feeds.txt -------------------------------------------------------
    candidatos = sorted(destino.glob("feeds.txt")) or sorted(destino.rglob("feeds.txt"))
    if not candidatos:
        notas.append("no encontré feeds.txt")
    else:
        f = candidatos[0]
        antes = f.read_text(encoding="utf-8")
        despues, motivo = nuevo_feeds_txt(antes, f"{BASE_FEEDS}/{xml}")
        notas.append(f"feeds.txt: {motivo}")
        if despues != antes:
            cambios.append(diff(antes, despues, f.relative_to(destino).as_posix()))
            f.write_text(despues, encoding="utf-8")

    # --- workflow --------------------------------------------------------
    for wf in sorted((destino / ".github" / "workflows").glob("*.yml")):
        antes = wf.read_text(encoding="utf-8")
        despues, motivo = nuevo_cron(antes, minuto)
        if despues != antes:
            notas.append(f"{wf.name}: {motivo}")
            cambios.append(diff(antes, despues, wf.relative_to(destino).as_posix()))
            wf.write_text(despues, encoding="utf-8")

    if not cambios:
        return " | ".join(notas) + "  (nada que cambiar)"

    print("".join(cambios))

    if not aplicar:
        return " | ".join(notas) + "  [simulación]"

    correr(["git", "add", "-A"], destino)
    r = correr(["git", "commit", "-m", "Migrar feed a fábrica propia; escalonar cron"], destino)
    if r.returncode != 0:
        return f"ERROR al hacer commit: {r.stderr.strip()}"
    r = correr(["git", "push"], destino)
    if r.returncode != 0:
        return f"ERROR al hacer push: {r.stderr.strip()}"
    return " | ".join(notas) + "  [APLICADO]"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true", help="hace commit y push (sin esto, solo simula)")
    ap.add_argument("--solo", metavar="REPO", help="procesa un único repo")
    args = ap.parse_args()

    if not shutil.which("gh"):
        print("Falta el GitHub CLI (gh). Instálalo y corre: gh auth login")
        return 2

    repos = {args.solo: REPOS[args.solo]} if args.solo else REPOS
    if args.solo and args.solo not in REPOS:
        print(f"«{args.solo}» no está en la lista.")
        return 2

    if not args.aplicar:
        print(">> SIMULACIÓN. Nada se escribe en GitHub. Añade --aplicar cuando el diff te convenza.\n")

    resumen = []
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        for repo, (xml, minuto) in repos.items():
            print(f"\n=== {repo} ".ljust(70, "="))
            resultado = procesar(repo, xml, minuto, args.aplicar, tmp)
            print("   ", resultado)
            resumen.append((repo, resultado))

    print("\n" + "=" * 70)
    for repo, resultado in resumen:
        print(f"{repo:28} {resultado}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
