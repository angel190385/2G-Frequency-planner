# Freq Planner 2G

Herramienta avanzada para la planificación y optimización de frecuencias GSM 2G desarrollada en Python y PyQt6.

## Características

- **Optimización Global con Algoritmo Genético**: Evaluación global simultánea de la red minimizando cocanalidades y adyacencias tanto en canales de control (BCCH) como de tráfico (TCH / hopping).
- **Reconfiguración y Adaptación de Espectro (Rango Inicial → Final)**:
  - Definición y detección de rango inicial y objetivo de frecuencias ARFCN.
  - Detección automática de frecuencias que desaparecen y su sustitución en todo el plan cargado.
  - Detección de nuevo espectro disponible para desatascar interferencias y mejorar globalmente la calidad de la red.
  - Modos de sustitución heurística inmediata y optimización global guiada.
- **Modelos Estadísticos Configurables de Pesos e Interferencia**:
  - **Logarítmico**: Compresión sublineal de varianza ($\log_2(1 + \text{HO})$), estándar para redes GSM urbanas/mixtas.
  - **Lineal Normalizado**: Ponderación directamente proporcional al tráfico de handovers respecto a la media de la red.
  - **Sigmoide / Curva en S**: Función logística centrada en la mediana, con alta sensibilidad ante umbrales críticos de tráfico.
  - **Exponencial / Pareto (80/20)**: Prioridad máxima a los principales hotspots y ejes de tráfico de la red.
  - **Uniforme / Clásico**: Tratamiento topológico equitativo sin ponderación de contadores de traspaso.
- **Dashboard e Informes Dinámicos HTML (3 Escenarios)**:
  - Generación de informes modernos e interactivos en formato HTML autocontenido (sin dependencias externas).
  - Selector en vivo para alternar entre el **Plan Actual (Original)** y hasta **3 Escenarios Calculados** (Score de coste, violaciones críticas BCCH-BCCH, violaciones totales, cocanalidades/adyacencias ponderadas por HO y % de mejora).
  - **Gráfica de barras interactiva por canal (ARFCN)** en HTML5 Canvas con desglose de portadoras BCCH, tráfico TCH, conflictos y buscador/inspector de celdas asignadas.
  - Explicación metodológica de los modelos estadísticos empleados.
  - Detalle completo de asignaciones por celda (Antes vs Después) con buscador y filtro de celdas modificadas en vivo.
  - Exportación directa a CSV y modo de impresión optimizado.
- **Página de Documentación Web para GitHub**:
  - Landing page profesional con simulador interactivo de pesos estadísticos en [`docs/index.html`](docs/index.html).
- **Gestión Integral de Red y Visualización**:
  - Matrices de vecindades y contadores de handovers.
  - Mapa interactivo de frecuencias y sectores.
  - Importación y exportación en formatos CSV y Excel (`.xlsx`).

## Requisitos

- Python 3.9+
- Dependencias indicadas en `requirements.txt`

## Instalación y Ejecución

```bash
# Instalar dependencias
pip install -r requirements.txt

# Ejecutar la aplicación
python main.py

# Ejecutar pruebas unitarias
python -m unittest discover tests
```
