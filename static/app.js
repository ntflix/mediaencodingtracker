function sortTable(column) {
  const table = document.getElementById('data-table');
  const rows = Array.from(table.rows).slice(1);
  rows.sort((a, b) => {
    const aText = a.cells[column].textContent;
    const bText = b.cells[column].textContent;
    return aText.localeCompare(bText);
  });
  rows.forEach(row => table.appendChild(row));
}