(function registerGridComparators(root) {
  const naturalCollator = new Intl.Collator(undefined, {
    numeric: true,
    sensitivity: 'base',
  });

  root.gridNaturalCompare = (left, right) => naturalCollator.compare(
    String(left ?? '').trim(),
    String(right ?? '').trim(),
  );

  root.gridNumberCompare = (left, right) => {
    const leftNumber = Number(left);
    const rightNumber = Number(right);
    const leftIsNumber = Number.isFinite(leftNumber);
    const rightIsNumber = Number.isFinite(rightNumber);
    if (leftIsNumber && rightIsNumber) return leftNumber - rightNumber;
    if (leftIsNumber) return -1;
    if (rightIsNumber) return 1;
    return root.gridNaturalCompare(left, right);
  };
})(typeof window === 'undefined' ? globalThis : window);
