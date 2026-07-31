const collaborationSeparators = ['（', '(', '：', ':', '—', '-', ' ']

export function collaborationReferencesDepartment(
  value: string,
  departmentName: string,
) {
  const item = value.trim()
  const name = departmentName.trim()
  return Boolean(name) && (
    item === name
    || collaborationSeparators.some(separator => item.startsWith(`${name}${separator}`))
  )
}

export function restrictCollaborations(
  values: string[],
  allowedDepartmentNames: string[],
) {
  const allowed = [...new Set(
    allowedDepartmentNames.map(name => name.trim()).filter(Boolean),
  )].sort((left, right) => right.length - left.length)
  return [...new Set(
    values
      .map(value => value.trim())
      .filter(value => (
        Boolean(value)
        && allowed.some(name => collaborationReferencesDepartment(value, name))
      )),
  )]
}

export function setCollaborationDepartment(
  values: string[],
  allowedDepartmentNames: string[],
  departmentName: string,
  selected: boolean,
) {
  const restricted = restrictCollaborations(values, allowedDepartmentNames)
  const withoutDepartment = restricted.filter(
    value => !collaborationReferencesDepartment(value, departmentName),
  )
  return selected ? [...withoutDepartment, departmentName] : withoutDepartment
}
