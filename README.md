# Parche Firmware Echo Mini

Parche standalone para el firmware `.IMG` del Echo Mini (RKnano) que habilita **animaciones de encendido y apagado personalizadas por tema**.

## ¿Qué hace?

Por defecto, sin importar el tema visual activo (A–E), el Echo Mini siempre muestra la misma animación de boot/shutdown (la del Tema A). Este parche modifica el firmware para que cada tema tenga su propio set de imágenes de encendido/apagado.

## Cambios que aplica

| # | Qué | Detalle |
|---|-----|---------|
| 1 | Expande tabla ROCK26 | De 1 bloque compartido a 5 bloques independientes (uno por tema) |
| 2 | Expande tabla de metadatos | Las entradas de boot se multiplican × 5, renombradas `T_X_*` |
| 3 | Parchea instrucción CMP | `CMP R0,#0x43` → `CMP R0,#0x00` — desactiva el salto forzado al Tema A |
| 4 | Parchea 4 instrucciones ADDW | Actualiza los offsets de bloque de `307×N` a `374×N` |
| 5 | Actualiza puntero `fw_end` | Copia el header de 512 bytes al nuevo final del archivo |

> **Nota sobre CRC32:** El Echo Mini incluye un trailer RKnano con CRC32, pero **no lo verifica activamente** al flashear. El parche omite intencionalmente el recálculo del CRC para evitar posibles problemas con alineación de tamaño de archivo.

## Requisitos

- Python 3.8 o superior
- Sin dependencias externas

## Uso

```bash
# Aplicar el parche (genera HIFIEC20_patched.IMG)
python patch_echo_mini.py HIFIEC20.IMG

# Guardar con nombre personalizado
python patch_echo_mini.py HIFIEC20.IMG -o firmware_modificado.IMG

# Solo verificar si el parche ya está aplicado
python patch_echo_mini.py HIFIEC20.IMG --check
```

## Ejemplo de salida

```
Cargando HIFIEC20.IMG …
  Recursos  : 1601
  Bloque    : 307 recursos/tema
  CMP offset: 0x1A3F4E
  Estado    : ⬜ sin parchear

Backup guardado en: HIFIEC20.IMG.bak

Aplicando parche …
  [████████████████████] 100%

✅ Parche aplicado correctamente
   CMP  : offset 0x1A3F4E  (0x2843 → 0x2800)
   ADDW : [307, 614, 921, 1228] → [374, 748, 1122, 1496]
   Tabla: 1601 → 1870 entradas
   Bloque: 307 → 374 recursos/tema

Archivo guardado en: HIFIEC20_patched.IMG
```

## Compatibilidad

Probado con firmware **Echo Mini v3.2.0** (`HIFIEC20.IMG`).  
El parche detecta automáticamente los offsets — no requiere configuración manual.
