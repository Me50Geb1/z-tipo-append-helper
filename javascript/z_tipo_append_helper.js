(function () {
    function rootForTipo(tabName) {
        return document.getElementById(`${tabName}_tipo_accordion`);
    }

    function inputValueFromLabeledControl(root, labelText) {
        if (!root) return null;
        const labels = Array.from(root.querySelectorAll('label, span'));
        const label = labels.find(el => (el.textContent || '').trim() === labelText);
        if (!label) return null;

        const candidates = [];
        let node = label;
        for (let i = 0; i < 5 && node; i++, node = node.parentElement) {
            candidates.push(node);
        }
        for (const container of candidates) {
            const input = container.querySelector('input');
            if (input && input.value) return input.value;
            const selected = container.querySelector('[role="combobox"] input, [role="combobox"]');
            if (selected) {
                const v = selected.value || selected.getAttribute('value') || selected.textContent;
                if (v && v.trim()) return v.trim();
            }
        }
        return null;
    }

    window.tipoAppendGetZTipoModel = function (tabName) {
        const root = rootForTipo(tabName);
        let value = inputValueFromLabeledControl(root, 'Model');
        if (value) return value;

        // Fallback for Gradio dropdown markup changes.
        if (root) {
            const comboboxes = Array.from(root.querySelectorAll('input[role="combobox"], [role="combobox"] input'));
            for (const input of comboboxes) {
                const v = input.value || '';
                if (v && (v.includes('/') || v.includes(' | ') || /tipo/i.test(v))) return v;
            }
        }
        return '';
    };

    window.tipoAppendInjectZTipoModel = function (tabName, args, modelIndex) {
        const model = window.tipoAppendGetZTipoModel(tabName);
        if (!model) {
            console.warn('TIPO Append Helper: Z-Tipo の Model 選択値を取得できませんでした。');
        } else {
            args[modelIndex] = model;
            console.log('TIPO Append Helper: Z-Tipo model =', model);
        }
        return args;
    };

})();

// Keep the helper directly below the original Z-Tipo accordion without
// modifying z-tipo-extension itself.
(function () {
    function placeHelperBelowTipo(tabName) {
        const tipoRoot = document.getElementById(`${tabName}_tipo_accordion`);
        const helperRoot = document.getElementById(`${tabName}_tipo_append_helper`);
        if (!tipoRoot || !helperRoot) return false;

        // Already in the desired location.
        if (tipoRoot.nextElementSibling === helperRoot) return true;

        // Move the existing Gradio node; event listeners/components remain intact.
        tipoRoot.insertAdjacentElement('afterend', helperRoot);
        return true;
    }

    function placeAllTipoHelpers() {
        placeHelperBelowTipo('txt2img');
        placeHelperBelowTipo('img2img');
    }

    function initPlacement() {
        placeAllTipoHelpers();
        // Gradio may rebuild portions of the UI. Observe changes and restore the
        // intended ordering only when needed.
        const app = (typeof gradioApp === 'function') ? gradioApp() : document.body;
        if (!app || app.dataset.tipoAppendOrderObserver === '1') return;
        app.dataset.tipoAppendOrderObserver = '1';
        const observer = new MutationObserver(() => placeAllTipoHelpers());
        observer.observe(app, { childList: true, subtree: true });
    }

    if (typeof onUiLoaded === 'function') {
        onUiLoaded(initPlacement);
    } else if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initPlacement, { once: true });
    } else {
        initPlacement();
    }
})();
