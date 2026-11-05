# Guía para agentes y colaboradores

## Alcance del proyecto

Este proyecto contiene el ETL independiente de reportes PE-14 del SENA Centro ASTIN y un tablero Streamlit para revisar la calidad de sus resultados. El código transforma archivos mensuales anchos a hechos y dimensiones CSV para consumo analítico.

## Estructura

- `pe14_etl/io.py`: lectura de XLSX, XLSM, CSV y SpreadsheetML; detección de cabecera y periodo; descubrimiento de fuentes.
- `pe14_etl/normalize.py`: parsing, unpivot, calidad por fila, dimensiones y verificaciones de contratos.
- `pe14_etl/categories.py`: catálogo y normalización de categorías poblacionales.
- `pe14_etl/contracts.py`: precondiciones, postcondiciones, invariantes y metadatos del módulo.
- `pe14_etl/persist.py`: exportación atómica de tablas y artefactos de control en carpetas separadas.
- `pe14_etl/cli.py` y `run_pe14.py`: interfaz de ejecución por archivo o carpeta.
- `dashboard.py`: tablero de validación que consume el manifiesto y los artefactos del ETL.
- `tests/`: pruebas unitarias, de contrato, integración, propiedades e idempotencia.

## Reglas de dominio que se deben preservar

- El hecho debe permanecer en formato estrecho: una fila por periodo, ficha y categoría poblacional con cantidad positiva.
- Las columnas Masc/Fem/NoBin son controles de consistencia. No deben convertirse en hechos independientes porque podrían duplicar conteos.
- Mantén las claves de dimensiones y las referencias de los hechos consistentes. Las salidas deben satisfacer unicidad, cantidades y horas no negativas y `ESTADO_CALIDAD` dentro del dominio permitido.
- Los valores anómalos deben quedar auditados y marcados para revisión sin detener el procesamiento del resto de filas cuando el contrato lo permita.
- Los identificadores y las salidas deben seguir siendo deterministas para la misma fuente. La escritura debe ser atómica por artefacto.
- Las nuevas categorías deben conservarse y quedar señaladas para revisión del catálogo; no descartarlas silenciosamente.
- La carpeta `--output` se reserva para las cinco tablas analíticas que consume Power BI. Auditoría, rechazados, metadatos y manifiesto pertenecen a `control_pe14` o a `--control-output`.
- No cambies los formatos de nombres `pe14_curated_YYYY_MM_*`, la separación de carpetas ni el manifiesto sin actualizar el tablero y sus pruebas.

## Datos institucionales y control de versiones

- Trata los archivos de `PE14/` como datos operativos potencialmente sensibles. No los publiques, copies a ejemplos, subas a GitHub ni incluyas en fixtures sin autorización expresa y revisión de desidentificación.
- No incluyas salidas generadas `pe14_curated_*`, metadatos que revelen rutas de usuario, archivos temporales ni el entorno `.venv` en el control de versiones.
- Para pruebas, genera fuentes pequeñas sintéticas en directorios temporales dentro del workspace y elimina esos artefactos al terminar.
- Antes de preparar un commit público, revisa tanto el contenido actual como el historial Git para detectar reportes originales, exportaciones, rutas privadas, credenciales u otros datos sensibles.

## Desarrollo y verificación

- Usa el entorno del proyecto `.venv` cuando exista. Instala dependencias con `python -m pip install -r requirements.txt`.
- Ejecuta el ETL desde la raíz con `python run_pe14.py`; acepta una ruta de archivo o carpeta como primer argumento.
- Ejecuta la suite con `python -m unittest discover -s tests -v`.
- Las pruebas del corpus real deben escribir resultados en una carpeta temporal, nunca junto a las fuentes.
- Al cambiar un contrato, parser o esquema, añade o ajusta pruebas que describan el comportamiento esperado. Conserva pruebas deterministas para propiedades e idempotencia.
- Al cambiar los artefactos de salida, valida tanto la integridad del ETL como los lectores del tablero.
- No afirmes que el tablero fue visualmente verificado si no se inició correctamente con Streamlit.

## Criterios de cambio

- Mantén separadas lectura, reglas de dominio, persistencia e interfaz de usuario.
- Prefiere cambios pequeños que mantengan compatibilidad con las columnas y nombres que consume Power BI.
- Documenta cualquier decisión que altere semántica de datos, interpretación de `TOTAL_HORAS`, claves, categorías o deduplicación.
- No debilites una precondición o postcondición para hacer pasar una prueba sin justificar el cambio de dominio.
