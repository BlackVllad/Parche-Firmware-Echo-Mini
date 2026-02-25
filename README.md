# Parche Firmware Echo Mini

Parche standalone para el firmware `.IMG` del Echo Mini (RKnano) que habilita **animaciones de encendido y apagado personalizadas por tema**.

## ¿Qué hace?

Por defecto, sin importar el tema visual activo (A–E), el Echo Mini siempre muestra la misma animación de boot/shutdown (la del Tema A). Este parche modifica el firmware para que cada tema tenga su propio set de imágenes de encendido/apagado.

---

## Diagrama: Parche de boot temático

```
FIRMWARE ORIGINAL (.IMG, 32MB)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
0x000000  ┌────────────────────┐
          │   HEADER 512B      │  magic: RKnanoFW
          │   fw_end=0x1FA0000 │
0x000200  ├────────────────────┤
          │   LOADER (ARM)     │
          │  ┌──────────────┐  │
          │  │ CMP R0,#0x43 │  │ ← offset 0x3DF5A
          │  │ ADDW #307    │  │ ← offsets 0x3DF6A..88
          │  │ ADDW #614    │  │   (uno por tema B/C/D/E)
          │  │ ADDW #921    │  │
          │  │ ADDW #1228   │  │
          │  └──────────────┘  │
0x598224  ├────────────────────┤
          │   PART4 / PART3    │
0x9B5998  ├────────────────────┤
          │   PART5 (recursos) │
          │  ┌──────────────┐  │
          │  │ ROCK26 table │  │  1602 entradas
          │  │ (índice BMP) │  │
          │  ├──────────────┤  │
          │  │ Bitmaps RGB  │  │  imágenes reales
          │  ├──────────────┤  │
          │  │ Metadata tbl │  │  1602 entradas × 108B
          │  └──────────────┘  │
0x1FA0000 ├────────────────────┤ ← fw_end
          │   HEADER COPY      │  copia de los primeros 512B
0x2000000 ├────────────────────┤
          │   3d94a194         │  trailer (4 bytes)
0x2000004 └────────────────────┘


APLICANDO EL PARCHE (patch_for_themed_boots)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PASO 1 — Leer 67 recursos compartidos de boot (índices 0-66)
  boot/charge/poweron images que se repiten en todos los temas

PASO 2 — Construir nuevas tablas (5 bloques × 374 entradas)
  ┌─────────────────────────────────────────────────────────┐
  │ Bloque A: [67 copia_boot_A] + [307 recursos_tema_A]     │
  │ Bloque B: [67 copia_boot_B] + [307 recursos_tema_B]     │
  │ Bloque C: [67 copia_boot_C] + [307 recursos_tema_C]     │
  │ Bloque D: [67 copia_boot_D] + [307 recursos_tema_D]     │
  │ Bloque E: [67 copia_boot_E] + [307 recursos_tema_E]     │
  └─────────────────────────────────────────────────────────┘
  Total: 5 × 374 = 1870 entradas  (antes: 1602)
  Copias B-E renombradas como T_B_*, T_C_*, T_D_*, T_E_*

PASO 3 — Parchear instrucción CMP en el Loader
  CMP R0, #0x43  →  CMP R0, #0x00
  (0x43 = 67 = SHARED_COUNT; el salto ya no excluye los índices de boot)

PASO 4 — Parchear 4 instrucciones ADDW
  ADDW #307  →  ADDW #374   (tema B: bloque empieza en índice 374)
  ADDW #614  →  ADDW #748   (tema C: bloque empieza en índice 748)
  ADDW #921  →  ADDW #1122  (tema D: bloque empieza en índice 1122)
  ADDW #1228 →  ADDW #1496  (tema E: bloque empieza en índice 1496)

PASO 5 — Escribir tablas expandidas en el archivo
  ROCK26: 1602 → 1870 entradas escritas en Part5
  Metadata: 1602 → 1870 entradas escritas en Part5

PASO 6 — Actualizar Part5 size en header[0x150]

PASO 7 — _fix_integrity() (ver diagrama abajo)


FIRMWARE PARCHEADO (.IMG, 54MB)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
0x000000  ┌────────────────────┐
          │   HEADER 512B      │  magic: RKnanoFW
          │   fw_end=0x3530000 │  ← actualizado
0x000200  ├────────────────────┤
          │   LOADER (ARM)     │
          │  ┌──────────────┐  │
          │  │ CMP R0,#0x00 │  │ ← PARCHEADO ✓
          │  │ ADDW #374    │  │ ← PARCHEADO ✓
          │  │ ADDW #748    │  │
          │  │ ADDW #1122   │  │
          │  │ ADDW #1496   │  │
          │  └──────────────┘  │
0x9B5998  ├────────────────────┤
          │   PART5 (recursos) │
          │  ┌──────────────┐  │
          │  │ ROCK26 table │  │  1870 entradas ✓
          │  ├──────────────┤  │
          │  │ Bitmaps RGB  │  │  sin cambios
          │  ├──────────────┤  │
          │  │ Metadata tbl │  │  1870 entradas ✓
          │  └──────────────┘  │
0x3530000 ├────────────────────┤ ← fw_end
          │   HEADER COPY      │  copia de los primeros 512B ✓
0x35FFFF4 ├────────────────────┤
          │   3d94a194         │  trailer preservado ✓
0x3600004 └────────────────────┘
```

---

## Diagrama: `_fix_integrity()` — Por qué el Echo Mini rechaza un .IMG corrupto

```
PROBLEMA: .IMG parcheado pero con fw_end fuera del archivo
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Echo Mini recibe el .IMG
        │
        ▼
Bootloader lee header[0x1F4] → fw_end
        │
        ▼
Busca copia del header en offset fw_end
        │
   ¿está dentro del archivo?
        │ NO → cancela update, borra el archivo ✗  (~1-3 seg)
        │ SÍ → continúa con el flasheo ✓


FLUJO DE _fix_integrity()
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ① Guardar trailer (últimos 4 bytes)
     saved = img_data[-4:]  → ej: 3d94a194
     (se guarda ANTES de cambiar el tamaño del archivo)
           │
           ▼
  ② Recalcular fw_end
     p5_end = Part5_offset + Part5_size
     si p5_end > fw_end actual:
       fw_end = redondear_arriba(p5_end, 64KB)
       escribir en header[0x1F4]
           │
           ▼
  ③ Ajustar tamaño del archivo    ← FIX CRÍTICO
     needed = redondear_arriba(fw_end + 16KB, 1MB) + 4
     archivo corto → extender con \x00
     archivo largo → recortar
     (garantiza que fw_end quede DENTRO del archivo)
           │
           ▼
  ④ Escribir header copy en fw_end
     img_data[fw_end : fw_end+0x200] = img_data[0:0x200]
     (512 bytes, el bootloader REQUIERE encontrarlos aquí)
           │
           ▼
  ⑤ Restaurar trailer
     img_data[-4:] = saved_trailer
     (CRC no verificado por el dispositivo → se deja intacto)


EJEMPLO REAL: HIFIEC20_boots.IMG
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ANTES (rechazado)          DESPUÉS (aceptado)
  ─────────────────          ──────────────────
  Tamaño : 53 MB             Tamaño : 54 MB
  fw_end : 0x3530000         fw_end : 0x3530000
  Archivo termina: 0x3525A68 Archivo termina: 0x3600004
  fw_end FUERA ✗             fw_end DENTRO ✓
  Header copy: NO ✗          Header copy: SÍ ✓
  Trailer: c618c618 ✗        Trailer: c618c618 (preservado)
```

---

## Cambios que aplica el parche

| # | Qué | Detalle |
|---|-----|---------|
| 1 | Expande tabla ROCK26 | 1602 → 1870 entradas (5 bloques × 374) |
| 2 | Expande tabla de metadatos | Las entradas de boot se multiplican × 5, renombradas `T_X_*` |
| 3 | Parchea instrucción CMP | `CMP R0,#0x43` → `CMP R0,#0x00` — desactiva el salto forzado al Tema A |
| 4 | Parchea 4 instrucciones ADDW | Offsets de bloque: `307×N` → `374×N` |
| 5 | Actualiza `fw_end` y header copy | `_fix_integrity()` garantiza que el bootloader encuentre el header |

> **Nota sobre CRC32:** El Echo Mini incluye un trailer RKnano con CRC32, pero **no lo verifica activamente** al flashear. El parche preserva el trailer original sin modificarlo.

---

## Reparar un .IMG que el Echo Mini rechaza

Si el dispositivo detecta el update pero lo cancela en 1-3 segundos:

```bash
python patch_echo_mini.py archivo_corrupto.IMG --fix
```

Esto aplica solo `fix_corrupt_firmware()` sin tocar el parche de boot:
- Recalcula `fw_end` desde el final real de Part5
- Extiende el archivo para que `fw_end` quede dentro
- Escribe la header copy en `fw_end`
- Preserva el trailer

---

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

# Reparar un .IMG que el Echo Mini rechaza
python patch_echo_mini.py archivo.IMG --fix
```

## Ejemplo de salida

```
Cargando HIFIEC20.IMG …
  Recursos  : 1602
  Bloque    : 307 recursos/tema
  CMP offset: 0x3DF5A  (valor: 0x2843)
  ADDW      : [307, 614, 921, 1228]
  Estado    : ⬜ sin parchear

Backup guardado en: HIFIEC20.IMG.bak

Aplicando parche …
  [████████████████████] 100%

✅ Parche aplicado correctamente
   CMP  : 0x3DF5A  (0x2843 → 0x2800)
   ADDW : [307, 614, 921, 1228] → [374, 748, 1122, 1496]
   Tabla: 1602 → 1870 entradas
   Bloque: 307 → 374 recursos/tema

Guardado en: HIFIEC20_patched.IMG
```

## Compatibilidad

Probado con firmware **Echo Mini v3.2.0** (`HIFIEC20.IMG`).  
El parche detecta automáticamente los offsets — no requiere configuración manual.

