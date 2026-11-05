# ETL PE-14 — SENA Centro ASTIN

El módulo procesa uno o varios reportes PE-14, detecta la cabecera institucional, transforma las columnas de categorías poblacionales mediante *unpivot* y publica hechos y dimensiones CSV aptos para Power BI.

## Ejecución

Desde la raíz del proyecto:

```powershell
python -m pip install -r requirements.txt
python run_pe14.py
```

Sin argumentos, se recorre de forma recursiva la carpeta `PE14` del proyecto y se procesa cada `.xlsx`, `.xlsm`, `.csv` o `.xml` compatible. Para especificar rutas:

```powershell
python run_pe14.py "C:\ruta\a\PE14"
python run_pe14.py "C:\ruta\a\PE-14_mayo 2026.xlsx" --output "C:\ruta\tablas_powerbi"
```

El periodo se infiere del nombre (`2026_05`, `mayo 2026`, fechas con año) o, como respaldo, de las filas institucionales anteriores a la cabecera. Un archivo sin periodo interpretable se reporta como error y no produce una salida de datos.

## Salidas

La carpeta indicada con `--output` contiene solo las cinco tablas CSV para Power BI: hechos de aprendices y dimensiones de ficha, centro, población y periodo. Por cada periodo se nombran `pe14_curated_YYYY_MM_*.csv`.

Auditorías, rechazos, metadatos y `pe14_curated_manifest.json` se guardan en `control_pe14`, en la raíz del proyecto. Puedes elegir otra ubicación con `--control-output "C:\ruta\control_pe14"`. Si el ETL encuentra auditorías, rechazados, metadatos o manifiestos de ejecuciones anteriores dentro de la carpeta de datos, los traslada a la carpeta de control para mantenerla limpia. Las tablas empiezan por `pe14_` y quedan excluidas de futuras entradas; repetir una ejecución reemplaza las mismas rutas de salida con el mismo contenido para una misma fuente.

Ejemplo para separar las tablas que leerá Power BI de los artefactos de control:

```powershell
python run_pe14.py "C:\ruta\a\archivos_PE14" `
  --output "C:\ruta\a\03_Reportes_PE14" `
  --control-output "C:\ruta\al\proyecto\control_pe14"
```

El hecho es esbelto: una fila por combinación periodo, ficha y categoría con cantidad positiva. Las métricas Masc/Fem/NoBin se conservan como controles de calidad y no se desdinamizan para evitar contar varias veces a los mismos aprendices.

## Contratos

El núcleo valida precondiciones de archivo, anchura, claves y mínimo de categorías; valida postcondiciones de atomicidad, unicidad e integridad referencial; y conserva identificadores deterministas. El parsing de `TOTAL_HORAS` registra valores anómalos en auditoría y marca filas para revisión. Los artefactos se escriben en UTF-8 con BOM para compatibilidad con Excel y de manera atómica archivo por archivo.

`TOTAL_HORAS` numérico se interpreta como horas por defecto. Si el origen registra esos números como fracción de día, se puede ejecutar con `--hours-numeric-mode days`.

## Pruebas y tablero de calidad

Ejecuta la suite incluida con la biblioteca estándar de Python:

```powershell
python -m unittest discover -s tests -v
```

La suite cubre parsers y detección de periodo (unitarias), pre/postcondiciones e integridad referencial (contratos), el corpus mensual y la generación de artefactos (integración), valores generados reproducibles (propiedades) y ejecución repetida con comparación byte a byte (idempotencia). La prueba de corpus usa `PE14` si la carpeta de muestras está presente; las salidas de prueba van a una carpeta temporal.

Para abrir el tablero local:

```powershell
python -m pip install -r requirements.txt
python -m streamlit run dashboard.py
```

El tablero consume `control_pe14/pe14_curated_manifest.json` y lee las tablas desde las rutas registradas en el manifiesto. Permite seleccionar periodo y revisar conteos, estados de calidad, diferencias contra gran total, rechazos, integridad referencial y huellas SHA-256 de los artefactos. Si usas una carpeta de control distinta, indícala en el campo lateral del tablero.

## Solución de problemas

### Python no encuentra `run_pe14.py`

Si aparece un error como:

```text
C:\Python314\python.exe: can't open file 'C:\Users\...\run_pe14.py': [Errno 2] No such file or directory
```

Python está buscando el script en la carpeta actual de PowerShell. Usa la ruta completa al intérprete del entorno virtual y al script para ejecutar el ETL desde cualquier ubicación:

```powershell
& "C:\Users\capatino\Documents\Proyecto Normalizador ETL PE14\.venv\Scripts\python.exe" `
  "C:\Users\capatino\Documents\Proyecto Normalizador ETL PE14\run_pe14.py" `
  "C:\ruta\a\la\carpeta\de\archivos PE-14" `
  --output "C:\ruta\a\la\carpeta\de\tablas Power BI" `
  --control-output "C:\Users\capatino\Documents\Proyecto Normalizador ETL PE14\control_pe14"
```

También puedes cambiar primero a la carpeta del proyecto y ejecutar el punto de entrada por nombre:

```powershell
Set-Location "C:\Users\capatino\Documents\Proyecto Normalizador ETL PE14"
& ".\.venv\Scripts\python.exe" run_pe14.py "C:\ruta\a\la\carpeta\de\archivos PE-14" --output "C:\ruta\a\la\carpeta\de\tablas Power BI" --control-output "C:\ruta\al\proyecto\control_pe14"
```

Sustituye las rutas de entrada y salida por las ubicaciones de tu equipo. En VS Code, selecciona `.venv\Scripts\python.exe` mediante **Python: Select Interpreter** para que el editor y la terminal usen las dependencias instaladas para el proyecto.

### El editor no resuelve `streamlit`

Confirma que el entorno virtual existe y contiene las dependencias:

```powershell
Set-Location "C:\Users\capatino\Documents\Proyecto Normalizador ETL PE14"
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt
```

Luego selecciona `.venv\Scripts\python.exe` como intérprete del proyecto y recarga la ventana del editor si el aviso persiste. Para iniciar el tablero, ejecuta `& ".\.venv\Scripts\python.exe" -m streamlit run dashboard.py` desde la raíz del proyecto.

### `python -m streamlit` responde `No module named streamlit`

Este mensaje suele indicar que `python` está apuntando a una instalación global (por ejemplo, `C:\Python314\python.exe`) en vez del entorno virtual, donde se instalaron las dependencias. Desde PowerShell, llama directamente al intérprete del proyecto:

```powershell
Set-Location "C:\Users\capatino\Documents\Proyecto Normalizador ETL PE14"
& ".\.venv\Scripts\python.exe" -m streamlit run dashboard.py
```

Como alternativa, activa el entorno y luego usa `python`:

```powershell
Set-Location "C:\Users\capatino\Documents\Proyecto Normalizador ETL PE14"
.\.venv\Scripts\Activate.ps1
python -m streamlit run dashboard.py
```

Para confirmar qué intérprete está activo, ejecuta `python -c "import sys; print(sys.executable)"`. La ruta debe terminar en `.venv\Scripts\python.exe`.
