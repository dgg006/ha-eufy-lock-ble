# eufy Smart Lock Touch T8510 por Bluetooth

> **Consumo de pilas en investigación.** Se informó un agotamiento prematuro de
> las pilas AA durante el uso de esta integración. La conexión persistente y las
> consultas frecuentes del historial podrían contribuir; la causa y su impacto
> todavía no están confirmados. Mientras se investiga, recomendamos desactivar
> **Mantener conectado** y usar una **comprobación cada 15–30 minutos**. Es una
> precaución, no una solución comprobada. Los valores predeterminados todavía
> mantienen la conexión y consultan cada cinco minutos; cambialos en
> **Ajustes → Dispositivos y servicios → eufy Smart Lock (BLE) → Configurar**.
> Sin conexión persistente, las acciones físicas pueden aparecer recién en la
> siguiente consulta exitosa; no dependas de avisos inmediatos de acceso. Los
> comandos de bloqueo y desbloqueo desde HA se conectan bajo demanda. Detectar
> interacciones por anuncios Bluetooth sigue en investigación y no está implementado.
> Las ideas y observaciones sin datos privados son bienvenidas en
> [GitHub Issues](https://github.com/dgg006/ha-eufy-lock-ble/issues).
> No publiques credenciales, historiales privados ni capturas sin revisar.

Integración comunitaria para Home Assistant basada en
[Leicas/ha-eufy-lock-ble](https://github.com/Leicas/ha-eufy-lock-ble), con licencia MIT.

**Modelo comprobado: T8510, Smart Lock Touch, firmware 2.0.2.3.** Se verificaron
bloqueo, desbloqueo, estado, batería, lectura de ajustes y mantenimiento de conexión.

Es el modelo Bluetooth alimentado por **4 pilas AA**, **sin Wi-Fi integrado**.
La versión de referencia se vendía **sin puente Wi-Fi**: el puente se compraba
por separado. También existieron paquetes con puente incluido; esta integración
no necesita ese accesorio.

[![Imagen oficial del modelo T8510, exterior e interior](https://cdn.shopify.com/s/files/1/0504/7094/4954/products/E130SmartLock.png?v=1698303947)](https://www.eufy.com/products/t8510)

Imagen del fabricante eufy/Anker. Algunas páginas lo llaman E130; verificá el código
**T8510** de la etiqueta. T8510P, T8520 y otros modelos parecidos no quedan
confirmados por su apariencia. [Compatibilidad y fuentes](docs/SUPPORTED_MODELS.md).

## Requisitos

- Home Assistant 2026.2.3 o posterior con Bluetooth. Prueba física realizada con 2026.10.1.
- Adaptador Bluetooth local o proxy ESPHome cercano, con conexiones activas habilitadas.
- Cerradura configurada previamente en la app oficial y acceso a sus credenciales.
- Fecha, hora y zona horaria correctas en la configuración general de Home Assistant.

No necesita el puente Wi-Fi de eufy. El uso normal es local; el acceso a la nube
de eufy se usa durante la configuración para obtener las credenciales. También
se pueden introducir manualmente.

## Instalación por HACS

[![Agregar a HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=dgg006&repository=ha-eufy-lock-ble&category=integration)

Con HACS instalado, pulsá el botón, confirmá el repositorio e instalá
**eufy Smart Lock (BLE)**. Luego reiniciá Home Assistant y agregá la integración
desde **Ajustes → Dispositivos y servicios**.

También podés agregarlo manualmente:

1. En HACS, abrí **Repositorios personalizados**.
2. Agregá `https://github.com/dgg006/ha-eufy-lock-ble`, categoría **Integración**.
3. Instalá **eufy Smart Lock (BLE)** y reiniciá Home Assistant.
4. En **Ajustes → Dispositivos y servicios**, agregá la integración.
5. Cerrá la app eufy mientras se conecta HA y seguí el formulario.

Conserva el dominio `eufy_lock_ble` para facilitar la migración. No instales esta
versión y la original simultáneamente. La instalación mediante repositorio
personalizado y la admisión al catálogo predeterminado de HACS son pasos distintos.

## Zona horaria automática

Se utiliza la zona configurada en **Home Assistant**, no la de la PC ni un país fijo.
Se calculan los dos campos del protocolo al iniciar cada sesión Bluetooth, incluido
el horario de verano cuando corresponda. No hay que introducir UTC−3 ni editar archivos.
El país solicitado al iniciar sesión en eufy selecciona el servidor de la cuenta;
no determina la zona horaria de la cerradura.

La versión 0.2.0 corrige dos valores que antes estaban fijados en UTC−4. Esa
inconsistencia podía permitir la conexión y luego provocar rechazos `0x42`.

## Entidades y límites

Permite bloquear y desbloquear, consultar estado y batería, y leer volumen,
cierre automático y opciones de protección. Los ajustes se muestran como lectura.
Desde 0.3.0 también consulta el historial por Bluetooth: último evento, método,
usuario y hora del último desbloqueo, y una entidad de actividad para automatizaciones.
El historial de los últimos 100 registros y sus bytes originales se pueden habilitar
como sensores adicionales. Los códigos desconocidos se conservan para analizarlos.

Los nombres e identificadores se muestran tal como los informa la cerradura.
Una acción «manual» no confirma si se usó la llave o el giro interior, ni quién lo hizo.
Al iniciar, los registros existentes se cargan sin disparar eventos; los nuevos
recibidos con más de dos minutos de retraso llevan `historical: true`.
[Detalles del historial y automatizaciones](docs/ACCESS_EVENTS.md).

### Nombres editables

En **Ajustes → Dispositivos y servicios → eufy Smart Lock (BLE) → Configurar**,
marcá **Editar nombres de usuarios**, elegí un usuario y escribí su **Nombre visible**.
Dejá el campo vacío para recuperar el nombre que informa la cerradura. Repetí el
proceso para otro usuario. Los alias son locales a Home Assistant y se aplican al
historial, los sensores y los próximos eventos sin reconectar Bluetooth.
El identificador de usuario y el nombre original siguen disponibles como atributos.

No administra usuarios, huellas ni códigos PIN, y no borra registros de acceso.

La cerradura acepta una conexión Bluetooth a la vez. El modo persistente permite
actualizaciones en vivo; el modo bajo demanda libera la conexión entre operaciones.

## Privacidad y soporte

Para investigar avisos sin conexión permanente, la versión 0.3.2 añade botones
de **captura Bluetooth** en el dispositivo. La captura dura diez minutos, permite
marcar pruebas físicas y guarda un archivo local en HA para analizarlo después.
Mientras captura, pausa las consultas automáticas; los marcadores no accionan
la cerradura. [Guía de captura](docs/BLUETOOTH_CAPTURE.md).

Cada usuario configura sus propios datos. La contraseña y el token de inicio de
sesión en eufy no se guardan en la entrada de configuración. Las credenciales de la
cerradura quedan en el almacenamiento local de HA. Los diagnósticos de la integración
ocultan identificadores y las trazas no incluyen cuerpos de paquetes BLE.

No publiques archivos de `.storage`, exportaciones de la cuenta ni capturas Bluetooth.
Los registros de componentes ajenos pueden contener identificadores: revisalos antes
de adjuntarlos. [Alcance de la privacidad](PRIVACY.md).

Al informar un problema, indicá modelo, firmware, versión de HA y código de error.
No hace falta incluir el número de serie completo, la MAC ni tus credenciales.

Proyecto comunitario independiente de eufy/Anker.
