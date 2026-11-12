(() => {
  const dataElement = document.getElementById('craft-plan-data');
  if (!dataElement) return;
  const data = JSON.parse(dataElement.textContent);
  const key = 'craft-progress:' + JSON.stringify(data);
  let progress = {};
  try { progress = JSON.parse(sessionStorage.getItem(key) || '{}'); } catch (_) {}
  const number = value => Number(value.toFixed(3)).toLocaleString('hu-HU');
  const plans = data.steps.filter(step => step.plan);
  function render() {
    let previousDone = true;
    data.steps.forEach(step => {
      if (!step.plan) { previousDone = false; return; }
      const card = document.getElementById('craft-step-' + step.key);
      const plan = step.plan;
      const saved = progress[step.key];
      const done = Math.max(0, Math.min(plan.trips, Number.isInteger(saved) ? saved : 0));
      progress[step.key] = done;
      const finished = done === plan.trips;
      card.querySelector('[data-status]').textContent = `${done} / ${plan.trips} kör kész · még ${plan.trips - done} kör`;
      const bar = card.querySelector('progress');
      bar.max = plan.trips;
      bar.value = done;
      card.classList.toggle('craft-complete', finished);
      card.querySelector('[data-complete]').disabled = finished || !previousDone;
      card.querySelector('[data-undo]').disabled = done === 0;
      card.querySelector('[data-next-title]').textContent = finished ? 'Elkészült' : !previousDone ? 'Az előző tétel után következik' : `Következő kör: ${number(done === plan.trips - 1 ? plan.last : plan.batch)} db`;
      const quantity = finished ? 0 : done === plan.trips - 1 ? plan.last : Math.min(plan.batch, step.qty);
      card.querySelector('[data-load]').textContent = finished ? '' : step.inputs.map(row => `${number(row.qty * quantity)} ${row.unit} ${row.name}`).join(' · ');
      card.querySelector('[data-load-weight]').textContent = finished ? '' : `Bevitt súly: ${number(plan.input_gram * quantity / 1000)} kg · Késztermék: ${number(plan.output_gram * quantity / 1000)} kg`;
      previousDone = previousDone && finished;
    });
    try { sessionStorage.setItem(key, JSON.stringify(progress)); } catch (_) {}
  }
  plans.forEach((step, index) => {
    const card = document.getElementById('craft-step-' + step.key);
    card.querySelector('[data-complete]').addEventListener('click', () => {
      progress[step.key] = Math.min(step.plan.trips, (progress[step.key] || 0) + 1);
      render();
    });
    card.querySelector('[data-undo]').addEventListener('click', () => {
      progress[step.key] = Math.max(0, (progress[step.key] || 0) - 1);
      plans.slice(index + 1).forEach(next => { progress[next.key] = 0; });
      render();
    });
  });
  render();
})();