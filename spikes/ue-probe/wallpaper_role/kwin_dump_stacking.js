// kwin_dump_stacking.js -- ADR-0029 sec.A self-diagnostic.
// Iterates KWin bottom->top stacking order; prints each window's index, class,
// caption, geometry to the journal (journalctl --user -t kwin_wayland).
// index 0 = BOTTOM/behind everything. dump_stacking.sh wraps this.
// PASS (plasma desktop role): PoC full-screen surface at/below index 0
//   (below the plasmashell desktop-icon view) -> behind icons + panel + windows.
// FAIL (the PoC-0a layer-shell result): PoC surface ABOVE the plasmashell desktop view.
function s(v){return (v===undefined||v===null)?"":(""+v);}
var list = workspace.stackingOrder;
print("=== KWIN STACKING ORDER (index 0 = BOTTOM/behind) -- " + list.length + " windows ===");
for (var i=0;i<list.length;i++){
  try{
    var c=list[i];
    var g=c.frameGeometry;
    var geo=g?(s(g.x)+","+s(g.y)+" "+s(g.width)+"x"+s(g.height)):(s(c.x)+","+s(c.y)+" "+s(c.width)+"x"+s(c.height));
    print("["+i+"] class='"+s(c.resourceClass)+"' name='"+s(c.resourceName)+"' caption='"+s(c.caption)+"' geo="+geo);
  }catch(ei){print("["+i+"] <unreadable: "+ei+">");}
}
print("=== END STACKING ORDER ===");
