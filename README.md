# Fábrica de feeds

Genera feeds RSS propios a partir de las páginas de categoría de
**rebelion.org** y los publica gratis en GitHub Pages.
Sustituye a PolitePaul: sin cuenta, sin cupos, sin feeds congelados.

Los repos de podcast no cambian de código: solo apuntan su `feeds.txt`
a la nueva URL y su `generar.py` (feedparser) sigue funcionando igual.

---

## Cómo funciona

Para cada fuente de `fuentes.json` el script intenta tres estrategias en orden
y se queda con la primera que devuelva entradas:

1. **Feed nativo de WordPress** — `<url_categoría>feed/`.
   Si existe, se copia tal cual (es la opción más fiel y con texto completo).
2. **API REST de WordPress** — `/wp-json/wp/v2/posts?categories=<id>`.
   Datos estructurados: título, enlace, fecha real y extracto.
3. **Raspado del HTML** de la página de categoría. Último recurso.

Regla de seguridad: **si una fuente falla, no se sobrescribe el XML anterior.**
Un feed viejo es inofensivo; un feed vacío haría que el pipeline no genere nada.

El estado de cada corrida queda en `docs/index.html` y `docs/estado.json`.

---

## Puesta en marcha (una sola vez)

1. Crea un repo nuevo en tu cuenta llamado **`feeds-rebelion`** y sube estos archivos.
2. `Settings → Pages → Source: Deploy from a branch`, rama `main`, carpeta `/docs`.
3. `Settings → Actions → General → Workflow permissions`: marca **Read and write permissions**.
4. Ve a la pestaña *Actions*, ejecuta **Generar feeds** a mano (`Run workflow`).
5. Abre `https://robertoroseteg-cloud.github.io/feeds-rebelion/` y revisa la tabla:
   ahí ves cuántas entradas trajo cada feed y por cuál de las tres vías.
6. Cambia el `feeds.txt` de cada repo de podcast según la tabla de abajo.

A partir de ahí el cron corre solo a las **11:00 UTC**, una hora antes que los
crons de los repos de podcast (12:03–12:37 UTC), para que siempre encuentren
contenido fresco. Hay una segunda pasada a las 23:00 UTC.

---

## Qué poner en cada `feeds.txt`

Base: `https://robertoroseteg-cloud.github.io/feeds-rebelion/`

| Repo | Nueva URL en `feeds.txt` |
|---|---|
| rebconlib | `…/rebconlib.xml` |
| rebcultura | `…/rebcultura.xml` |
| rebecosoc | `…/rebecosoc.xml` |
| rebeconomia | `…/rebeconomia.xml` |
| rebfeminismo | `…/rebfeminismo.xml` |
| rebmedioslib | `…/rebmedioslib.xml` |
| reb-otro-mundo-es-posible | `…/rebotromundo.xml` |
| rebAL | `…/rebAL.xml` |
| rebargentina | `…/rebargentina.xml` |
| rebcolombia | `…/rebcolombia.xml` |
| rebcuba **y** reb-cuba | `…/rebcuba.xml` |
| rebecuador **y** reb-ecuador | `…/rebecuador.xml` |
| reb-mexico | `…/rebmexico.xml` |
| rebvenezuela | `…/rebvenezuela.xml` |

`desinfomex`, `desinfointerna` y `desinfo_columnas` no se tocan: sus feeds
funcionan bien y no dependen de PolitePaul.

`desinfoopinion` **tampoco se toca, por ahora**: desinformemonos.org está
detrás de Anubis y no se pudo generar su XML. Ver «Anubis» más abajo.

Los pares `rebcuba`/`reb-cuba` y `rebecuador`/`reb-ecuador` comparten fuente,
igual que antes en PolitePaul. Si te sobra uno, es buen momento para archivarlo.

---

## Probar en local antes de subir

```bash
pip install -r requirements.txt
python3 generar_feeds.py                 # todas las fuentes
python3 generar_feeds.py rebecuador.xml  # solo una
```

Comprobación rápida de qué vía funciona para una categoría, sin el script:

```bash
UA='rebpodcast-feedbot/1.0'
curl -sI -A "$UA" https://rebelion.org/categoria/territorios/america-latina-y-caribe/ecuador/feed/ | head -1
curl -s  -A "$UA" 'https://rebelion.org/wp-json/wp/v2/categories?slug=ecuador' | head -c 300
```

Si el `feed/` devuelve `200` y XML, no necesitas nada más para esa fuente:
puedes poner esa URL directamente en `feeds.txt` y saltarte la fábrica.
Si devuelve `410 Gone` (es lo que veo hoy en `https://rebelion.org/feed/`),
los feeds nativos están desactivados en el sitio y entran las vías 2 y 3.

---

## Notas de mantenimiento

**Anubis (desinformemonos.org) — sin resolver.** El sitio devuelve el reto de
proof-of-work en las tres puertas: el feed, la API REST y el HTML de la
categoría. La hipótesis es que Anubis solo desafía a los agentes que se
anuncian como navegador, y por eso el script se identifica como
`rebpodcast-feedbot/1.0`, *sin* la palabra "Mozilla" — pero **eso no se ha
comprobado con una petición real**. Hasta comprobarlo, `desinfoopinion` queda
fuera de `fuentes.json` y fuera de la migración. Para probarlo:

```
curl -A "rebpodcast-feedbot/1.0" -o /dev/null -w "%{http_code}\n" \
     "https://desinformemonos.org/categoria/opinion/feed/"
```

Si pasa, vuelve a añadir la fuente y su repo. Si tu `generar.py` usa un UA tipo
navegador, cámbialo también ahí (`feedparser.parse(url, agent="…")`).

**Si un feed sale con artículos que no son de la categoría**, añade un
`"selector"` CSS a esa fuente en `fuentes.json` para acotar el raspado:

```json
{ "salida": "rebcuba.xml", "…": "…", "selector": "main article" }
```

**Si un feed sale vacío**, revisa `docs/estado.json`: dice qué vía se intentó.
Lo más probable es que cambiara el HTML del sitio; ajusta el selector.

**PolitePaul**: una vez que los 14 feeds de aquí funcionen, puedes borrar los
feeds congelados de tu cuenta —o cancelar el plan— sin perder nada.

**Cuidado con la cortesía**: son ~14 peticiones dos veces al día a rebelion.org.
Es tráfico despreciable, pero no bajes el cron a cada hora sin necesidad.

---

## Verificado contra el sitio real (2026-09-07)

| Comprobación | Resultado |
|---|---|
| `…/categoria/tema/cultura/feed/` | **HTTP 410**. El feed nativo de WordPress está desactivado en todo rebelion.org: la estrategia 1 nunca va a servir. |
| `/wp-json/wp/v2/categories` | **Abierta**. Es la vía buena para las 14 fuentes. |
| `/wp-json/wp/v2/posts?categories=28` | Devuelve artículos reales y recientes. |
| desinformemonos.org | Anubis bloquea feed, REST y HTML. Sin resolver. |

`id` de categoría en rebelion.org (el script los deduce solo del slug; se anotan
por si algún día hace falta fijarlos a mano):

| slug | id | slug | id |
|---|---|---|---|
| conocimiento-libre | 37 | america-latina-y-caribe | 7 |
| cultura | 316 | argentina | 8 |
| ecologia-social | 3 | colombia | 12 |
| economia | 24 | cuba | 13 |
| feminismos | 29 | ecuador | 28 |
| mentiras-y-medios | 4 | mexico | 25 |
| otro-mundo-es-posible | 5 | venezuela | 18 |
