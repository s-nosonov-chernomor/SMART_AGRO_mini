// C:\PyCharmProjects\agrosmart ЗОНА 3\Зона 3\app\static\scripts\main2.js


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

            btn.classList.toggle('on', on);
            btn.classList.toggle('off', !on);
            btn.setAttribute('aria-pressed', on ? 'true' : 'false');
            btn.dataset.state = on ? 'on' : 'off';
            btn.title = on ? 'вкл' : 'откл';
        };

        const updateDynamicControls = () => {
            document.querySelectorAll('.status-button').forEach(btn => {
                const key = btn.id;
                if (!key || !(key in data)) return;

                const on = isOn(data[key]);

                btn.classList.toggle('on', on);
                btn.classList.toggle('off', !on);
                btn.setAttribute('aria-pressed', on ? 'true' : 'false');
                btn.dataset.state = on ? 'on' : 'off';
                btn.title = on ? 'вкл' : 'откл';
            });

            document.querySelectorAll('.dynamic-value-btn').forEach(btn => {
                const key = btn.id;
                if (!key || !(key in data)) return;

                const valueNode = btn.querySelector('.value-btn-current');
                if (!valueNode) return;

                const raw = data[key];
                const num = parseFloat(raw);

                if (!isNaN(num)) {
                    valueNode.textContent = num.toFixed(0);
                } else {
                    valueNode.textContent = raw;
                }
            });
        };

        const updateText = (id, key) => {
            const elem = document.getElementById(id);
            if (!elem) return;

            const raw = data[key];
            const num = parseFloat(raw);

            if (!isNaN(num)) {
                elem.textContent = num.toFixed(0);
            } else {
                elem.textContent = raw;
            }
        };

        const updateText2 = (id, key) => {
            const elem = document.getElementById(id);
            if (!elem) return;

            const raw = data[key];
            const num = parseFloat(raw);

            if (!isNaN(num)) {
                elem.textContent = num.toFixed(1);
            } else {
                elem.textContent = raw;
            }
        };

        const updateStatusText = (id, key) => {
            const elem = document.getElementById(id);
            if (!elem) return;
            elem.textContent = isOn(data[key]) ? 'вкл' : 'откл';
        };

        const updateTimeText = (id, key) => {
            const elem = document.getElementById(id);
            if (!elem) return;

            const value = parseFloat(data[key]) || 0;
            elem.textContent = (value / 10).toFixed(0);
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

        // ДИНАМИЧЕСКИЕ КНОПКИ И ПОЛЯ ИЗ БД
        updateDynamicControls();

        // ОСТАЛЬНОЕ ОСТАВЛЯЕМ КАК БЫЛО
        updateText2('UNIT_ID_PH', 'Уровень PH');
        updateText2('UNIT_ID_EC', 'Уровень EC');

        updateBinaryIndicator('indicator-level-1', 'Уровень бак максимум');
        updateBinaryIndicator('indicator-level-2', 'Уровень бак средний');
        updateBinaryIndicator('indicator-level-3', 'Уровень бак минимум');

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

function openModal(parameterName, minValue = 0, maxValue = 100, stepValue = 1, currentValue = 0) {
    const nameEl = document.getElementById('parameter-name');
    const valueEl = document.getElementById('parameter-value');
    const valueLabelEl = document.getElementById('parameter-value-label');
    const modalEl = document.getElementById('valueModal');

    if (!nameEl || !valueEl || !valueLabelEl || !modalEl) return;

    nameEl.value = parameterName;
    valueEl.min = minValue;
    valueEl.max = maxValue;
    valueEl.step = stepValue;
    valueEl.value = currentValue;

    valueLabelEl.textContent = currentValue;
    modalEl.style.display = 'block';
}

function syncValueLabel() {
    const valueEl = document.getElementById('parameter-value');
    const valueLabelEl = document.getElementById('parameter-value-label');
    if (!valueEl || !valueLabelEl) return;
    valueLabelEl.textContent = valueEl.value;
}

function changeSliderBy(delta) {
    const valueEl = document.getElementById('parameter-value');
    if (!valueEl) return;

    const step = parseFloat(valueEl.step || '1');
    const min = parseFloat(valueEl.min || '0');
    const max = parseFloat(valueEl.max || '100');
    let current = parseFloat(valueEl.value || '0');

    current += delta * step;
    current = Math.max(min, Math.min(max, current));

    valueEl.value = current;
    syncValueLabel();
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
