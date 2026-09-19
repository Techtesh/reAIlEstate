import { calculatedRoomArea, configurationArea, configurationPrice, type Configuration } from './buyer'

export function LayoutTable({ layouts }: { layouts: Configuration[] }) {
  if (!layouts.length) return <p>Layout data unknown.</p>
  return <>
    <div className="table-scroll"><table className="layout-summary-table">
      <thead><tr><th scope="col">Layout</th><th scope="col">Carpet area</th><th scope="col">Calculated room area</th><th scope="col">Price</th></tr></thead>
      <tbody>{layouts.map((layout, index) => <tr key={layout.id ?? index}>
        <th scope="row">{layout.name || layout.configurationType || 'Unnamed layout'}</th>
        <td>{configurationArea(layout)}</td>
        <td>{calculatedRoomArea(layout.roomRectangleSumSqFt)}</td>
        <td>{configurationPrice(layout)}</td>
      </tr>)}</tbody>
    </table></div>
    <p className="help-text">Calculated room area sums available room length &times; breadth measurements in sq ft. It may include decks or overlapping spaces and is not carpet area.</p>
  </>
}
