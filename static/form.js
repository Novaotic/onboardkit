// form.js — client-side helpers for OnboardKit
// All logic is progressive-enhancement only; the form works without JS.

document.addEventListener('DOMContentLoaded', () => {
  wireRadioToggle('needs_computer_yes', 'needs_computer_no', 'computerSection');
  wireRadioToggle('alarm_yes', 'alarm_no', 'alarmFacilitiesSection');
  highlightChecked();
});

function wireRadioToggle(yesId, noId, sectionId) {
  const yesEl   = document.getElementById(yesId);
  const noEl    = document.getElementById(noId);
  const section = document.getElementById(sectionId);
  if (!yesEl || !noEl || !section) return;

  function update() {
    section.style.display = yesEl.checked ? '' : 'none';
  }
  yesEl.addEventListener('change', update);
  noEl.addEventListener('change', update);
  update();
}

function highlightChecked() {
  const inputs = document.querySelectorAll(
    '.checkbox-label input[type="checkbox"], .radio-label input[type="radio"]'
  );

  inputs.forEach(input => {
    const label = input.closest('.checkbox-label, .radio-label');
    if (!label) return;

    function sync() {
      label.style.borderColor = input.checked ? 'var(--primary)' : '';
      label.style.background  = input.checked ? 'var(--primary-light)' : '';
    }
    input.addEventListener('change', () => {
      if (input.type === 'radio') {
        document.querySelectorAll(`input[name="${input.name}"]`).forEach(sibling => {
          const sibLabel = sibling.closest('.checkbox-label, .radio-label');
          if (sibLabel) {
            sibLabel.style.borderColor = '';
            sibLabel.style.background  = '';
          }
        });
      }
      sync();
    });
    sync();
  });
}
