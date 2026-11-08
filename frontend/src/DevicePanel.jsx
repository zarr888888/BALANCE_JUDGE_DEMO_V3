import { useEffect, useState } from 'react'

async function api(path,body){const r=await fetch('/api/iot/v2'+path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});const b=await r.json();if(!r.ok)throw new Error(b.message||b.error);return b}
const fmt=v=>v==null?'—':new Intl.NumberFormat('id-ID',{maximumFractionDigits:2}).format(v)
const terminal=s=>['COMPLETED','FAILED','CANCELLED','EXPIRED','FAILED_REBOOT','FAILED_TIMEOUT'].includes(s)
export default function DevicePanel({plan,role}){
  const [devices,setDevices]=useState([]),[commands,setCommands]=useState([]),[enabled,setEnabled]=useState(false),[mode,setMode]=useState('DRY_RUN'),[busy,setBusy]=useState(false),[error,setError]=useState('')
  useEffect(()=>{
    if(role!=='operator')return
    let live=true
    const refresh=()=>Promise.all([api('/devices'),api('/commands')]).then(([d,c])=>{if(live){setDevices(d.devices);setEnabled(d.miniature_enabled);setCommands(c.commands)}}).catch(e=>{if(live)setError(e.message)})
    refresh();const timer=setInterval(refresh,3000)
    return()=>{live=false;clearInterval(timer)}
  },[role])
  async function queue(){setBusy(true);setError('');try{await api('/commands',{device_id:'esp32-demo',plan_id:plan.plan_id,mode});setCommands((await api('/commands')).commands)}catch(e){setError(e.message)}finally{setBusy(false)}}
  async function cancel(id){setBusy(true);try{await api(`/commands/${id}/cancel`,{});setCommands((await api('/commands')).commands)}catch(e){setError(e.message)}finally{setBusy(false)}}
  if(role!=='operator')return <section className="panel"><h3>Demo juri</h3><p>Coba prediksi kebutuhan, pembagian air, jadwal, dan telemetri simulasi. Prototipe IoT didemonstrasikan terpisah; demo publik ini tidak mengendalikan perangkat fisik.</p></section>
  return <section className="panel"><h3>Sambungan miniatur ESP32</h3><p className="muted">Satu sensor flow berada di sumber. Volume sumber yang terukur dicatat terpisah dari perkiraan penerimaan zona.</p>
    {devices.length===0?<p className="empty">Menunggu heartbeat firmware balance-iot/2.0. Unggah firmware V2 dan isi alamat backend serta kunci perangkat.</p>:devices.map(d=><div key={d.device_id} className="notice"><strong>{d.device_id} · {d.online?'ONLINE':'OFFLINE'}</strong> · ARM {d.armed?'aktif':'mati'} · jarak ultrasonik {fmt(d.source_distance_cm)} cm · release sejak boot {fmt(d.released_since_boot_ml)} mL · diterima {fmt(d.age_s)} s lalu<br/>Kalibrasi: {d.calibration_ref||'Belum ditandai'} · nilai belum diverifikasi server</div>)}
    <div className="replay-form"><label>Mode pengiriman<select value={mode} onChange={e=>setMode(e.target.value)}><option value="DRY_RUN">DRY_RUN · tanpa gerakan alat</option><option value="MINIATUR" disabled={!enabled}>MINIATUR · kuota fisik sumber</option></select></label>
      <button className="primary-button" disabled={busy||!plan} onClick={queue}>{busy?'Memproses...':mode==='DRY_RUN'?'Kirim rencana DRY_RUN':'Kirim rencana ke miniatur'}</button></div>
    <p className="integration-caption">Rencana aktif: {plan?.plan_id||'Hitung rekomendasi dahulu'}. Satu rencana dipakai paling banyak satu kali per perangkat. Batas 1000 mL total dan 750 mL per zona. MINIATUR memerlukan pengaktifan server, kalibrasi, dan ARM lokal melalui Serial Monitor.</p>
    {error&&<div role="alert" className="integration-error">{error}</div>}
    <div className="table-scroll"><table className="history-table"><thead><tr><th>Perintah / mode</th><th>Status</th><th>Release dilaporkan A/B/C</th><th>Aksi</th></tr></thead><tbody>{commands.map(c=><tr key={c.command_id}><td><code>{c.command_id.slice(0,15)}…</code><br/>{c.mode}</td><td>{c.status}<br/>{c.last_message||''}</td><td>{['A','B','C'].map(z=><div key={z}>{z}: {fmt(c.reported_release_ml?.[z])} mL</div>)}<small>{c.mode==='DRY_RUN'?'SIMULASI':'Pembacaan sumber; bukan penerimaan petak'}</small></td><td>{!terminal(c.status)&&<button className="secondary-button" onClick={()=>cancel(c.command_id)} disabled={busy}>Minta penghentian</button>}</td></tr>)}</tbody></table></div>
    <p className="integration-caption">Penghentian jaringan diproses pada checkpoint firmware. STOP pada Serial Monitor dan pemutus daya pompa dipakai untuk penghentian langsung di meja uji. Prediksi serta eta tetap berasal dari model sintetis.</p>
  </section>
}
