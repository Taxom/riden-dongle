# Источники и принятые решения

Протокол сверен 2026-11-09 (America/Toronto). В 0.1.1 от 2026-11-12 исправлены запуск при выключенном БП и обработка отсутствующих сущностей карточкой; транспорт и карта записей не менялись.

## Прошивка

При первоначальной проверке 2026-11-09 снимок ниже совпадал с тогдашним `main`. Это историческая база интеграции, а не утверждение о текущей ветке.

- [Снимок исходников](https://github.com/Taxom/riden-dongle/tree/5b457d33164399e639663d96b5ab84dcafbedf0e)
- [Карта регистров](https://github.com/Taxom/riden-dongle/blob/5b457d33164399e639663d96b5ab84dcafbedf0e/include/riden_modbus/riden_modbus_registers.h)
- [Коэффициенты RD6006P и преобразования](https://github.com/Taxom/riden-dongle/blob/5b457d33164399e639663d96b5ab84dcafbedf0e/src/riden_modbus/riden_modbus.cpp)
- [TCP-мост, исключения и отсутствие повторов записи](https://github.com/Taxom/riden-dongle/blob/5b457d33164399e639663d96b5ab84dcafbedf0e/src/riden_modbus_bridge/riden_modbus_bridge.cpp)

## Home Assistant

- [Стандартный Modbus](https://www.home-assistant.io/integrations/modbus/)
- [Общий coordinator](https://developers.home-assistant.io/docs/integration_fetching_data/)
- [Config flow](https://developers.home-assistant.io/docs/config_entries_config_flow_handler/)
- [Options flow](https://developers.home-assistant.io/docs/config_entries_options_flow_handler/)
- [Number](https://developers.home-assistant.io/docs/core/entity/number/)
- [Диагностика](https://developers.home-assistant.io/docs/core/integration_diagnostics/)
- [REST API и history/period](https://developers.home-assistant.io/docs/api/rest/)
- [Core 2026.11.1](https://github.com/home-assistant/core/releases/tag/2026.11.1)

Стандартный Modbus предоставляет датчики, переключатели с verify и действия записи регистров. Готовой платформы number для двух уставок в его документированном наборе нет. Нужное поведение можно собирать из помощников/шаблонов, но здесь выбрана собственная интеграция: одно устройство, числовые сущности, общий опрос, приоритет управления, обязательное чтение после записи и ясный исход потерянного ACK в одном месте.

Использован небольшой асинхронный клиент FC3/FC6 на стандартной библиотеке Python: ограниченная карта записей, строгая проверка MBAP/PDU, одна очередь и отсутствие автоматических повторов FC6 видны непосредственно в коде. Pymodbus и внешние Python-зависимости не требуются.

В Core 2026.11.1 схемы форм используют probatio. Код выбирает probatio, если он установлен, и voluptuous в старых средах; это само по себе не является проверкой совместимости со всеми старыми версиями HA. Поля опций проверены также на сериализацию для реального интерфейса HA.

Для существующей конфигурации теперь используется `coordinator.async_refresh()` при установке, с заранее заданным пустым снимком и корректной недоступностью после ошибки. Этот вариант описан в официальной документации Fetching data для случая, когда ошибка первого чтения не должна останавливать установку. Первичное добавление через config flow по-прежнему проверяет включённый БП только чтением.

## Батарейный канал, 0.1.2 от 2026-11-24

- [Руководство производителя RD6006P](https://www.rcscomponents.kiev.ua/datasheets/RD6006P-info.pdf): раздел 1.1 — батарейный канал 0,01 В, точность ±(0,5% + 3 единицы младшего разряда); раздел 1.4.2.1 — отдельная зелёная клемма и счётчики Ah/Wh, сбрасываемые при выключении питания.
- [Пример RD6006P Modbus](https://github.com/MDBInd/RD6006P/blob/main/rd6006p.yaml): адрес 33, коэффициент 0,01.
- Текущая база Wi-Fi-прошивки: Taxom/riden-dongle, SHA 9593e5b093a810e79970a37b9dc30870a33cf853 (taxom.10). Поправка taxom.11 меняет только чтение/отображение напряжения батареи и тесты.

Режим зарядки — флаг из регистра 32, не определение химии батареи, процента заряда или завершения зарядки. Ah/Wh — показания штатных счётчиков БП.

В стандартном протоколе регистр 33 (0x0021) передаёт V × 100:
[описание Modbus от Joy-IT](https://joy-it.net/files/files/Produkte/JT-PS1440-C-Pro/MODBUS_Protocol_RD60xx_PSxx.pdf).
Экран UniSoft может показывать тысячные вольта, отсутствующие в этом регистре.
Спецификация штатной прошивки не является проверкой точности измерительного
канала конкретной версии UniSoft; программное масштабирование не калибрует АЦП.
