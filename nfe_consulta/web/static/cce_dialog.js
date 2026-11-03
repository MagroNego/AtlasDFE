(() => {
  const dialog = document.getElementById('cce-dialog');
  if (!dialog) return;
  const body = dialog.querySelector('[data-cce-body]');
  document.querySelectorAll('[data-cce-open]').forEach((button) => {
    button.addEventListener('click', () => {
      const content = button.parentElement.querySelector('[data-cce-content]');
      body.replaceChildren(content.content.cloneNode(true));
      dialog.showModal();
    });
  });
  dialog.querySelector('[data-cce-close]').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', (event) => {
    if (event.target !== dialog) return;
    const rect = dialog.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
  });
})();
