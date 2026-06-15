#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests
from shapely.geometry import shape
from tqdm import tqdm

API_BASE = "https://idg.ine.gob.bo/api"
DEFAULT_OUTPUT = Path("datos/comunidades.parquet")
DEFAULT_MUNICIPIOS = Path("recursos/municipios.csv")

BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:144.0) Gecko/20100101 Firefox/144.0",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.5",
    "Content-Type": "application/json",
    "Origin": "https://idg.ine.gob.bo",
    "Referer": "https://idg.ine.gob.bo/geoportal",
}

LOGGER = logging.getLogger("descargar_comunidades")


class TqdmLoggingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            tqdm.write(msg)
            self.flush()
        except Exception:
            self.handleError(record)


class TokenManager:
    def __init__(self, timeout: float):
        self.timeout = timeout
        self._token: str | None = None
        self._lock = threading.Lock()

    def get_token(self, force_refresh: bool = False) -> str:
        with self._lock:
            if self._token is None or force_refresh:
                self._token = self._fetch_token()
            return self._token

    def _fetch_token(self) -> str:
        response = requests.post(
            f"{API_BASE}/auth/acceso",
            headers=BASE_HEADERS,
            json={},
            timeout=self.timeout,
        )
        response.raise_for_status()
        token = response.json().get("token")
        if not token:
            raise RuntimeError("auth/acceso did not return token")
        return token


_thread_local = threading.local()


def get_session() -> requests.Session:
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        _thread_local.session = session
    return session


def parse_resultado_final_item(
    feature: dict,
    municipio_meta: dict[str, str],
) -> dict | None:
    codigo = feature.get("id") or feature.get("codigo")
    nombre = feature.get("nombre")
    geojson_value = feature.get("geojson")

    if not codigo or not nombre or geojson_value is None:
        return None

    if isinstance(geojson_value, str):
        geometry = shape(json.loads(geojson_value))
    else:
        geometry = shape(geojson_value)

    return {
        "departamento": municipio_meta["departamento"],
        "municipio": municipio_meta["municipio"],
        "nombre": nombre,
        "codigo": codigo,
        "geometry": geometry,
    }


def fetch_comunidades_municipio(
    codigo: int,
    municipio_meta: dict[str, str],
    token_manager: TokenManager,
    timeout: float,
    max_attempts: int,
) -> list[dict]:
    payload = {"id": f"{codigo:06d}", "mara": 2024, "tipo": "cpv"}
    session = get_session()
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        token = token_manager.get_token(force_refresh=False)
        headers = {**BASE_HEADERS, "Authorization": f"Bearer {token}"}

        try:
            response = session.post(
                f"{API_BASE}/ficha/contornoMapa",
                headers=headers,
                json=payload,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            last_error = exc
            if attempt == max_attempts:
                raise
            time.sleep(min(2**attempt, 10))
            continue

        if response.status_code == 200:
            data = response.json()
            resultado_final = data.get("resultadoFinal")
            if not resultado_final:
                last_error = RuntimeError(
                    f"Municipio {codigo} devolvió resultadoFinal vacío"
                )
                if attempt < max_attempts:
                    LOGGER.warning(
                        "Municipio %s devolvió resultadoFinal vacío en intento %s/%s; reintentando",
                        codigo,
                        attempt,
                        max_attempts,
                    )
                    time.sleep(min(2**attempt, 10))
                    continue
                raise last_error

            records = []
            skipped = 0
            for feature in resultado_final:
                try:
                    parsed = parse_resultado_final_item(feature, municipio_meta)
                except Exception:
                    skipped += 1
                    continue
                if parsed is None:
                    skipped += 1
                    continue
                records.append(parsed)
            if skipped:
                LOGGER.warning(
                    "Municipio %s: %s items de resultadoFinal fueron omitidos por formato inesperado",
                    codigo,
                    skipped,
                )
            if not records:
                last_error = RuntimeError(
                    f"Municipio {codigo} no produjo comunidades parseables"
                )
                if attempt < max_attempts:
                    LOGGER.warning(
                        "Municipio %s no produjo comunidades parseables en intento %s/%s; reintentando",
                        codigo,
                        attempt,
                        max_attempts,
                    )
                    time.sleep(min(2**attempt, 10))
                    continue
                raise last_error
            return records

        if response.status_code in (401, 403):
            token_manager.get_token(force_refresh=True)
            if attempt == max_attempts:
                response.raise_for_status()
            continue

        if response.status_code >= 500 and attempt < max_attempts:
            time.sleep(min(2**attempt, 10))
            continue

        response.raise_for_status()

    if last_error is not None:
        raise last_error
    raise RuntimeError(f"No se pudo descargar comunidades del municipio {codigo}")


def build_gdf(records: list[dict]) -> gpd.GeoDataFrame:
    gdf = gpd.GeoDataFrame(records, geometry="geometry", crs="EPSG:4326")
    gdf = gdf[["departamento", "municipio", "nombre", "codigo", "geometry"]]
    return gdf.sort_values(["codigo", "nombre"]).reset_index(drop=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Descarga comunidades censales y guarda datos/comunidades.parquet."
    )
    parser.add_argument(
        "--municipios",
        type=Path,
        default=DEFAULT_MUNICIPIOS,
        help="CSV con municipios y columna codigo.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Ruta del geoparquet de salida.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Número de municipios a descargar en paralelo.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="Timeout en segundos por request.",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=5,
        help="Intentos máximos por municipio.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Nivel de logging.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    handler = TqdmLoggingHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        handlers=[handler],
        force=True,
    )

    municipios = pd.read_csv(args.municipios, index_col="codigo")
    municipios.index = municipios.index.astype(int)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    LOGGER.info("Cargando token público del IDG")
    token_manager = TokenManager(timeout=args.timeout)
    token_manager.get_token()

    LOGGER.info(
        "Descargando %s municipios con %s workers hacia %s",
        len(municipios),
        args.workers,
        args.output,
    )

    records: list[dict] = []
    total_comunidades = 0

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(
                fetch_comunidades_municipio,
                int(codigo),
                {
                    "departamento": row["departamento"],
                    "municipio": row["municipio"],
                },
                token_manager,
                args.timeout,
                args.max_attempts,
            ): int(codigo)
            for codigo, row in municipios.iterrows()
        }

        with tqdm(total=len(futures), desc="Municipios", unit="municipio") as progress:
            for future in as_completed(futures):
                codigo = futures[future]
                comunidad_records = future.result()
                records.extend(comunidad_records)
                total_comunidades += len(comunidad_records)
                progress.update(1)
                progress.set_postfix(comunidades=total_comunidades)
                LOGGER.info(
                    "Municipio %s completado: %s comunidades acumuladas",
                    codigo,
                    total_comunidades,
                )

    LOGGER.info("Construyendo GeoDataFrame con %s comunidades", total_comunidades)
    gdf = build_gdf(records)

    LOGGER.info("Guardando geoparquet en %s", args.output)
    gdf.to_parquet(args.output)

    LOGGER.info(
        "Descarga completada: %s municipios, %s comunidades",
        len(municipios),
        len(gdf),
    )


if __name__ == "__main__":
    main()
