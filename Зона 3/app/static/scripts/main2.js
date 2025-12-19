// нормализуем "включено": сервер может прислать '1', '1.0' или 1
const isOn = (v) => v === '1' || v === '1.0' || v === 1;

async function updateData() {
    try {
        const response = await fetch(getParametersUrl);
        const data = await response.json();

        const updateButton = (id, key) => {
          const btn = document.getElementById(id);
          if (!btn) return;
          const on = isOn(data[key]);

          // меняем только состояние
          btn.classList.toggle('on',  on);
          btn.classList.toggle('off', !on);

          // доступность/хуки
          btn.setAttribute('aria-pressed', on ? 'true' : 'false');
          btn.dataset.state = on ? 'on' : 'off';

          // опционально: подсказка состояния при наведении (текст кнопки не меняем)
          btn.title = on ? 'вкл' : 'откл';
        };
        const updateText = (id, key) => {
            const elem = document.getElementById(id);
            if (!elem) return;

            const raw = data[key];
            const num = parseFloat(raw);
            if (!isNaN(num)) {
                // округляем до десятых
                elem.textContent = num.toFixed(0);
            } else {
                // не число — показываем как есть
                elem.textContent = raw;
            }
        };
        const updateText2 = (id, key) => {
            const elem = document.getElementById(id);
            if (!elem) return;

            const raw = data[key];
            const num = parseFloat(raw);
            if (!isNaN(num)) {
                // округляем до десятых
                elem.textContent = num.toFixed(1);
            } else {
                // не число — показываем как есть
                elem.textContent = raw;
            }
        };


        const updateStatusText = (id, key) => {
            const elem = document.getElementById(id);
            if (elem) {
                elem.textContent = data[key] === '1' ? 'вкл' : 'откл';
            }
        };

        const updateTimeText = (id, key) => {
            const elem = document.getElementById(id);
            if (elem) {
                const value = parseFloat(data[key]) || 0;
                elem.textContent = (value / 10).toFixed(0);
            }
        };

        const updateBinaryIndicator = (id, key) => {
          const indicator = document.getElementById(id);
          if (!indicator) return;
          if (isOn(data[key])) {
            indicator.classList.remove('empty');
            indicator.classList.add('filled');
          } else {
            indicator.classList.remove('filled');
            indicator.classList.add('empty');
          }
        };


        updateButton('napolnit', 'Наполнение');
        updateButton('peremesh', 'Перемешивание');
        updateButton('sliv', 'Дренаж');
        updateButton('nasos', 'Насос');
        updateButton('svet1', 'Свет 1');
        updateButton('svet2', 'Свет 2');

        updateButton('polka1', 'Стеллаж 1');
        updateButton('polka2', 'Стеллаж 2');
        updateButton('polka3', 'Стеллаж 3');
        updateButton('polka4', 'Стеллаж 4');
        updateButton('polka5', 'Стеллаж 5');
        updateButton('polka6', 'Стеллаж 6');

        updateButton('mode_param', 'Режим эксплуатации');
        updateButton('mixing', 'Растворный узел');

        updateButton('chanel_1_1_white', 'КАНАЛ 1 СИНИЙ');
        updateText('level_1_1_white',    'ЯРКОСТЬ 1 СИНИЙ');
        updateButton('chanel_1_1_red',   'КАНАЛ 1 КРАСНЫЙ');
        updateText('level_1_1_red',      'ЯРКОСТЬ 1 КРАСНЫЙ');
        updateButton('chanel_1_2_white', 'КАНАЛ 2 СИНИЙ');
        updateText('level_1_2_white',    'ЯРКОСТЬ 2 СИНИЙ');
        updateButton('chanel_1_2_red',   'КАНАЛ 2 КРАСНЫЙ');
        updateText('level_1_2_red',      'ЯРКОСТЬ 2 КРАСНЫЙ');
        updateButton('chanel_1_3_white', 'КАНАЛ 3 СИНИЙ');
        updateText('level_1_3_white',    'ЯРКОСТЬ 3 СИНИЙ');
        updateButton('chanel_1_3_red',   'КАНАЛ 3 КРАСНЫЙ');
        updateText('level_1_3_red',      'ЯРКОСТЬ 3 КРАСНЫЙ');
        updateButton('chanel_1_4_white', 'КАНАЛ 4 СИНИЙ');
        updateText('level_1_4_white',    'ЯРКОСТЬ 4 СИНИЙ');
        updateButton('chanel_1_4_red',   'КАНАЛ 4 КРАСНЫЙ');
        updateText('level_1_4_red',      'ЯРКОСТЬ 4 КРАСНЫЙ');

        updateText2('UNIT_ID_PH', 'Уровень PH');
        updateText2('UNIT_ID_EC', 'Уровень EC');

        // Обновляем графические индикаторы для основного бака
        updateBinaryIndicator('indicator-level-1', 'Уровень бак максимум');
        updateBinaryIndicator('indicator-level-2', 'Уровень бак средний');
        updateBinaryIndicator('indicator-level-3', 'Уровень бак минимум');


        // Обновляем графические индикаторы для баков компонентов

        // updateBinaryIndicator('indicator-level-A1', 'Уровень А максимум');
        // updateBinaryIndicator('indicator-level-B1', 'Уровень В максимум');
        // updateBinaryIndicator('indicator-level-K1', 'Уровень К максимум');

        updateBinaryIndicator('indicator-level-A2', 'Уровень А минимум');
        updateBinaryIndicator('indicator-level-B2', 'Уровень В минимум');
        updateBinaryIndicator('indicator-level-K2', 'Уровень К минимум');

        updateStatusText('statusA', 'Подача А в бак');
        updateStatusText('statusB', 'Подача В в бак');
        updateStatusText('statusK', 'Подача кислоты в бак');
        updateTimeText('timeA', 'Время подачи A в бак');
        updateTimeText('timeB', 'Время подачи В в бак');
        updateTimeText('timeK', 'Время подачи кислоты в бак');

    } catch (error) {
        console.error("Ошибка обновления данных:", error);
    }
}

async function toggleParameter(parameterName) {
    try {
        const response = await fetch(toggleParameterUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ parameter: parameterName })
        });
        await response.json();
        updateData();
    } catch (error) {
        console.error("Ошибка переключения параметра:", error);
    }
}

async function setValue() {
    const parameterName = document.getElementById('parameter-name').value;
    const parameterValue = document.getElementById('parameter-value').value;
    try {
        const response = await fetch(setParameterValueUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ parameter: parameterName, value: parameterValue })
        });
        await response.json();
        closeModal();
        updateData();
    } catch (error) {
        console.error("Ошибка установки значения:", error);
    }
}

function openModal(parameterName) {
    document.getElementById('parameter-name').value = parameterName;
    document.getElementById('parameter-value').value = '';
    document.getElementById('valueModal').style.display = 'block';
}

function closeModal() {
    document.getElementById('valueModal').style.display = 'none';
}

window.onclick = function(event) {
    const modal = document.getElementById('valueModal');
    if (event.target === modal) {
        closeModal();
    }
};

const brightnessInput = document.getElementById('parameter-value');
if (brightnessInput) {
    brightnessInput.addEventListener('keydown', function(event) {
        if (event.key === 'Enter') {
            event.preventDefault();
            setValue();
        }
    });
}

setInterval(updateData, 1000);
