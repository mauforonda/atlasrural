## Datos del Censo 2024 en Bolivia a nivel de comunidad

> Una reproducción del mismo [trabajo que hice para manzanos](https://github.com/mauforonda/atlasurbano/), esta vez con comunidades en áreas dispersas.

El [geoportal oficial](https://idg.ine.gob.bo/) de resultados censales permite consultar datos para cada comunidad. En este repositorio descargo estos datos y construyo un mapa para observar patrones espaciales desde ellos.

Podemos consultar el número de personas y viviendas para cada comunidad, pero el INE sólo nos permite descargar más información en casos donde hayan suficientes personas, por razones de privacidad.

## Datos

Ofrezco 3 conjuntos de datos:

[comunidades.parquet](datos/comunidades.parquet): un geoparquet con las coordenadas del centro aproximado de cada comunidad.

[poblacion.parquet](datos/poblacion.parquet): un parquet con el número de personas y viviendas reportadas en cada comunidad y un indicador si existe una ficha disponible (`validado`).

[fichas.parquet](datos/fichas.parquet): un parquet con la ficha completa para comunidades donde es posible descargarla.

## Descarga

Para construir estos datos, escribí 2 cuadernos:

- [Descarga de polígonos](descargar_comunidades.py)
- [Descarga de datos](descargar_fichas.py)

Estos cuadernos dependen de [un listado de municipios](recursos/municipios.csv) y [un diccionario de los campos en cada ficha](recursos/campos.json).

🌱

