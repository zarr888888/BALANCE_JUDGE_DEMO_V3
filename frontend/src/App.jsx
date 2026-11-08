import { useState, useEffect } from 'react'
import { ResponsiveContainer, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend } from 'recharts'
import DevicePanel from './DevicePanel.jsx'
import './App.css'
import './Integration.css'
import './Polish.css'
import BrandMark from './BrandMark.jsx'
import NavIcon from './NavIcon.jsx'

const menus = [['overview','▦','Dashboard'],['zones','⌖','Peta Zona'],['monitoring','▤','Monitoring'],['schedule','◷','Jadwal Irigasi'],['analytics','⌁','Analitik AI'],['history','◴','Riwayat'],['settings','⚙','Pengaturan']]
const defaults = {prediction_mode:'ml',supply_fraction:1,tail_capacity_ml:5000,loss_mode:'learned',loss_flow_ml_s:6,loss_head_cm:20,horizon_s:600}
const positions = {A:'Hulu',B:'Tengah',C:'Hilir'}
const statusLabels = {NEEDS_APPROVAL:'Siap ditinjau',NO_SUPPLY:'Tanpa pasokan',DEGRADED_TAIL:'Perlindungan hilir perlu ditinjau'}
const num = (v,d=2) => v == null ? '—' : new Intl.NumberFormat('id-ID',{maximumFractionDigits:d}).format(v)
const percent = v => v == null ? null : v*100
const time = v => v ? new Date(v).toLocaleString('id-ID') : 'Tidak tersedia'
async function api(path,body,method) {
  const r=await fetch(`/api${path}`,{method:method||(body===undefined?'GET':'POST'),credentials:'same-origin',headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)})
  const data=await r.json()
  if(!r.ok) throw new Error(r.status===401?'Sesi berakhir atau data masuk salah. Silakan masuk kembali.':data.message||data.error||`HTTP ${r.status}`)
  return data
}
function Metric({label,value,note}) {return <article className="summary-card"><div className="card-heading"><span>{label}</span></div><h3>{value}</h3><p>{note}</p></article>}
function Json({value}) {return <details className="json-details"><summary>Lihat JSON untuk review</summary><pre>{JSON.stringify(value,null,2)}</pre></details>}
function Table({heads,children}) {return <div className="table-scroll"><table className="history-table"><thead><tr>{heads.map(h=><th key={h}>{h}</th>)}</tr></thead><tbody>{children}</tbody></table></div>}
function Schedule({schedule}) {
  return <section className="panel"><h3>Jadwal rencana alokasi saat ini</h3><p className="muted">Waktu relatif dalam detik. Debit, setup, dan waktu tempuh masih asumsi. Satu zona dilayani setiap waktu.</p>
    {schedule.slots.length===0 ? <p className="empty">Tidak ada slot distribusi pada rencana ini.</p> : <>
      <div className="timeline" aria-label="Diagram jadwal distribusi">{schedule.slots.map(s=><div className="timeline-row" key={s.zone_id}><strong>Zona {s.zone_id}</strong><div className="timeline-track"><div className={`timeline-bar zone-${s.zone_id}`} style={{left:`${s.start_s/schedule.end_s*100}%`,width:`${(s.release_end_s-s.start_s)/schedule.end_s*100}%`}} /></div><span>{num(s.start_s)}–{num(s.release_end_s)} s</span></div>)}</div>
      <Table heads={['Urutan / zona','Release (mL demo)','Mulai (s)','Release selesai (s)','Verifikasi selesai (s)']}>{schedule.slots.map(s=><tr key={s.zone_id}><td>{s.order} · {s.zone_id} / {positions[s.zone_id]}</td><td>{num(s.release_ml)}</td><td>{num(s.start_s)}</td><td>{num(s.release_end_s)}</td><td>{num(s.verification_end_s)}</td></tr>)}</Table>
    </>}
  </section>
}

function App() {
  const [menu,setMenu]=useState('overview'), [logged,setLogged]=useState(false), [checking,setChecking]=useState(true)
  const [role,setRole]=useState(null), [judgeLogin,setJudgeLogin]=useState(()=>new URLSearchParams(window.location.search).get('operator')!=='1'), [username,setUsername]=useState('juri'), [password,setPassword]=useState('')
  const [key,setKey]=useState(''), [status,setStatus]=useState(null), [plan,setPlan]=useState(null), [options,setOptions]=useState(defaults)
  const [custom,setCustom]=useState(false), [scenario,setScenario]=useState(null), [history,setHistory]=useState([]), [telemetry,setTelemetry]=useState([])
  const [error,setError]=useState(''), [busy,setBusy]=useState(false), [refreshing,setRefreshing]=useState(false), [loaded,setLoaded]=useState(false)
  const [shortfall,setShortfall]=useState(20), [replay,setReplay]=useState(null), [replayError,setReplayError]=useState('')
  useEffect(()=>{
    let live=true
    Promise.all([api('/status'),api('/session')]).then(([s,a])=>{if(live){setStatus(s);setRole(a.role);setLogged(a.authenticated);setChecking(false)}}).catch(e=>{if(live){setError(`Backend belum terhubung: ${e.message}`);setChecking(false)}})
    return ()=>{live=false}
  },[])
  useEffect(()=>{
    if(!logged)return
    let live=true
    const latest=fetch('/api/plan/latest').then(r=>{if(r.status===404)return null;if(!r.ok)throw new Error('Gagal memuat rencana terakhir.');return r.json()})
    Promise.all([api('/scenario'),api('/history'),api('/iot/data'),latest]).then(([s,h,t,p])=>{if(live){setScenario(s);setHistory(h);setTelemetry(t.data);setPlan(p);setLoaded(true)}}).catch(e=>{if(live)setError(e.message)})
    return ()=>{live=false}
  },[logged])
  useEffect(()=>{
    if(!logged||menu!=='monitoring')return
    let live=true
    const id=setInterval(()=>api('/iot/data').then(t=>{if(live)setTelemetry(t.data)}).catch(e=>{if(live)setError(e.message)}),5000)
    return ()=>{live=false;clearInterval(id)}
  },[logged,menu])
  async function login(e){e.preventDefault();setBusy(true);setError('');try{await api('/session',judgeLogin?{username,password}:{dashboard_key:key});const session=await api('/session');setRole(session.role);setKey('');setPassword('');setLogged(true)}catch(e){setError(e.message)}finally{setBusy(false)}}
  async function logout(){try{await api('/session',undefined,'DELETE');setLogged(false);setLoaded(false);setPlan(null);setReplay(null)}catch(e){setError(e.message)}}
  async function calculate(){
    setBusy(true);setError('');setPlan(null)
    try{const requestId=globalThis.crypto?.randomUUID?.()||`web-${Date.now()}-${Math.random().toString(16).slice(2)}`
      const body={...options,request_id:requestId,context:'SIMULASI'};if(custom)body.feature_scenario=scenario
      const result=await api('/plan',body);setPlan(result);setHistory(await api('/history'))
    }catch(e){setError(`Tidak ada hasil baru. ${e.message}`)}finally{setBusy(false)}
  }
  async function monitor(simulate=false){setRefreshing(true);setError('');try{if(simulate)await api('/iot/simulate',{});setTelemetry((await api('/iot/data')).data)}catch(e){setError(e.message)}finally{setRefreshing(false)}}
  async function runReplay(){setBusy(true);setReplayError('');setReplay(null);try{setReplay(await api('/scheduler/simulate',{received_factor:1-Number(shortfall)/100,supply_fraction:options.supply_fraction,horizon_s:options.horizon_s}))}catch(e){setReplayError(`Replay perlu ditinjau. ${e.message}`)}finally{setBusy(false)}}
  function option(name,v){setOptions(o=>({...o,[name]:v===''?null:Number(v)}))}
  function feature(zone,name,value){setScenario(s=>{const next=structuredClone(s),f=zone==='supply'?next.supply:next.zones.find(z=>z.zone_id===zone).features;f[name]=value===''?null:Number(value)
    if(zone!=='supply'){f.etc_forecast_L=f.area_m2*f.kc_assumed*f.et0_forecast_mm;f.percolation_assumed_L=f.area_m2*f.percolation_assumed_mm;f.effective_rain_forecast_L=f.area_m2*f.effective_rain_forecast_mm}return next})}
  const zones=plan?.allocation.zones||[]
  const chart=zones.map(z=>({name:`Zona ${z.zone_id}`,'LP (%)':percent(z.fulfillment_ratio),'Proporsional (%)':percent(plan.comparison_baseline.zones.find(b=>b.zone_id===z.zone_id)?.fulfillment_ratio)}))
  const metrics=plan&&<div className="summary-grid integration-metrics">
    <Metric label="Pasokan tersedia" value={`${num(plan.source_budget.available_demo_ml)} mL`} note="Skala demo · sumber bersama" />
    <Metric label="Alokasi LP" value={`${num(plan.allocation.total_release_ml)} mL`} note={`Sisa budget ${num(plan.allocation.unused_ml)} mL`} />
    <Metric label="Disparitas proporsional" value={`${num(percent(plan.comparison_baseline.disparity_ratio))} pp`} note="Input kebutuhan dan eta yang sama" />
    <Metric label="Disparitas LP" value={`${num(percent(plan.allocation.disparity_ratio))} pp`} note="Poin persentase rasio pemenuhan" />
  </div>
  const allocation=plan&&<section className="panel"><div className="panel-heading"><div><h3>Rekomendasi pembagian air</h3><p className="muted">Status: {statusLabels[plan.allocation.status]||plan.allocation.status} · dihitung {time(plan.created_at)}</p></div><span className={`status-badge allocation-status ${plan.allocation.status.toLowerCase()}`}>Rekomendasi</span></div>
    {plan.allocation.status==='NO_SUPPLY'&&<div className="notice">Tidak ada pasokan. Disparitas nol tidak menunjukkan kebutuhan terpenuhi.</div>}
    {plan.allocation.status==='DEGRADED_TAIL'&&<div className="integration-error">Target hilir belum terpenuhi. Tinjau kapasitas serta kebijakan sebelum digunakan.</div>}
    <Table heads={['Zona','Kebutuhan','Efisiensi (η)','Alokasi LP','Perkiraan diterima','Pemenuhan LP']}>{zones.map(z=><tr key={z.zone_id}><td><strong>{z.zone_id} · {positions[z.zone_id]}</strong></td><td>{num(z.need_ml)} mL</td><td>{num(plan.eta.zones.find(e=>e.zone_id===z.zone_id).delivery_efficiency,5)}</td><td>{num(z.release_ml)} mL</td><td>{num(z.expected_delivered_ml)} mL</td><td>{num(percent(z.fulfillment_ratio))}%</td></tr>)}</Table>
    <p className="integration-caption">Semua volume di tabel dalam mL demo. Perkiraan diterima = eta × release; belum merupakan pengukuran perangkat.</p>
    <details className="json-details"><summary>Alasan setiap zona</summary>{zones.map(z=><p key={z.zone_id}>{z.explanation_id}</p>)}</details>
  </section>
  if(!logged)return <main className="login-page"><section className="login-card"><div className="login-brand"><BrandMark/><span>BALANCE</span></div><h1>Masuk ke dasbor</h1><p>Perencanaan pembagian air untuk hulu, tengah, dan hilir.</p>
    <div className="notice">Demo ini memakai data simulasi. Masuk dengan akun juri untuk mencoba prediksi, alokasi, dan jadwal.</div><button type="button" className="secondary-button" onClick={()=>setJudgeLogin(v=>!v)}>{judgeLogin?"Gunakan kunci operator":"Gunakan akun demo juri"}</button>
    <form onSubmit={login}><>{judgeLogin?<><label>Username<input value={username} autoComplete="username" onChange={e=>setUsername(e.target.value)} required/></label><label>Password<input type="password" autoComplete="current-password" value={password} onChange={e=>setPassword(e.target.value)} required/></label></>:<><label htmlFor="dashboard-key">Kunci dasbor</label><input id="dashboard-key" type="password" autoComplete="off" value={key} onChange={e=>setKey(e.target.value)} required /></>}</>
      <p className="muted">{judgeLogin?"Gunakan akun uji coba yang disertakan dalam pengumpulan lomba.":<>Gunakan kunci operator yang dikelola oleh tim BALANCE.</>}</p><button className="primary-button" disabled={busy||checking}>{busy?'Memeriksa...':'Masuk dasbor'}</button></form>
    {error&&<div role="alert" className="integration-error">{error}</div>}<p className="muted">Backend: {status?.status||(checking?'Memeriksa...':'Belum terhubung')}</p></section></main>

  return <div className="app-shell"><aside className="sidebar"><div className="brand"><BrandMark/><div><h1>BALANCE</h1><p>Perencanaan irigasi</p></div></div><div className="admin-label">{role==='judge'?'AKSES DEMO JURI':'RUANG OPERATOR'}</div>
    <nav className="navigation">{menus.map(([id,,label])=><button key={id} onClick={()=>setMenu(id)} className={`nav-item ${menu===id?'active':''}`} aria-current={menu===id?'page':undefined}><span className="nav-icon"><NavIcon name={id}/></span>{label}</button>)}</nav><div className="sidebar-footer"><div className="status-dot" /><div><strong>Mode Simulasi</strong><p>Prediksi dan optimasi aktif</p></div></div></aside>
    <main className="main-content"><header className="topbar"><div><p className="breadcrumb">BALANCE / {role==='judge'?'DEMO JURI':'OPERATOR'}</p><h2>{menus.find(m=>m[0]===menu)[2]}</h2></div><button className="secondary-button" onClick={logout}>Keluar</button></header>
      <section className="page-content"><div className="welcome-row"><div><p className="eyebrow">SISTEM OPTIMASI IRIGASI</p><h2>{menu==='overview'?'Dashboard BALANCE':menus.find(m=>m[0]===menu)[2]}</h2><p className="muted">Rencanakan distribusi, tinjau pemenuhan setiap zona.</p></div><span className="demo-badge">SIMULASI · BELUM DIVALIDASI LAPANGAN</span></div>
        <div className="notice dashboard-status">Layanan AI siap · Data simulasi · Akses {role==='judge'?'demo juri':'operator'}</div><div className="dashboard-info">Prediksi menggunakan model yang dilatih dengan data sintetis. Alokasi ditampilkan dalam mL skala demo; perangkat IoT didemonstrasikan secara terpisah pada tahap ini.</div>
        {error&&<div role="alert" className="integration-error">{error}</div>}
        {menu==='overview'&&<><section className="panel integration-hero"><div><p className="eyebrow">PERENCANAAN DISTRIBUSI</p><h3>Rencana pembagian air tiga zona</h3><p>Bandingkan pembagian proporsional dengan optimasi LP berdasarkan kebutuhan dan efisiensi setiap saluran.</p></div><button className="primary-button" disabled={busy||!loaded} onClick={calculate}>{busy?'Menghitung...':'Hitung rekomendasi'}</button></section>
          {!plan&&<div className="panel empty">Belum ada hasil aktif. Tekan Hitung rekomendasi atau atur skenario di Analitik AI.</div>}{metrics}{allocation}
          {plan&&<><section className="panel"><h3>Perbandingan tingkat pemenuhan</h3><p className="muted">Persentase kebutuhan yang diperkirakan terpenuhi, bukan persentase akurasi model.</p><div className="integration-chart"><ResponsiveContainer width="100%" height="100%"><BarChart data={chart}><CartesianGrid strokeDasharray="3 3" vertical={false}/><XAxis dataKey="name"/><YAxis domain={[0,100]} unit="%"/><Tooltip formatter={v=>`${num(v)}%`}/><Legend/><Bar dataKey="Proporsional (%)" fill="#a5b8d0" radius={[5,5,0,0]}/><Bar dataKey="LP (%)" fill="#2864f0" radius={[5,5,0,0]}/></BarChart></ResponsiveContainer></div></section><Schedule schedule={plan.schedule}/></>}
        </>}
        {menu==='analytics'&&<><section className="panel"><h3>Atur skenario AI</h3><p className="muted">Sesuaikan pasokan dan kondisi saluran, lalu hitung ulang. Setiap hasil menyimpan parameter yang digunakan.</p>
          <form onSubmit={e=>{e.preventDefault();calculate()}}><fieldset disabled={busy||!loaded} className="integration-fieldset"><div className="integration-form">
            <label>Metode prediksi<select value={options.prediction_mode} onChange={e=>setOptions(o=>({...o,prediction_mode:e.target.value}))}><option value="ml">Machine learning · model sintetis</option><option value="baseline">Baseline · agronomi dan pasokan sebelumnya</option></select></label>
            <label>Sumber eta<select value={options.loss_mode} onChange={e=>setOptions(o=>({...o,loss_mode:e.target.value}))}><option value="learned">Prediksi efisiensi · model sintetis</option><option value="assumed">Asumsi tetap</option></select></label>
            <label>Pasokan simulasi (%)<input type="number" min="0" max="100" step="any" required value={options.supply_fraction===null?'':options.supply_fraction*100} onChange={e=>option('supply_fraction',e.target.value===''?'':Number(e.target.value)/100)}/></label>
            {[['tail_capacity_ml','Batas release hilir (mL demo)',0],['loss_flow_ml_s','Debit nominal loss (mL/s)',0.001],['loss_head_cm','Head sumber loss (cm)',0.001],['horizon_s','Horizon jadwal (detik)',0.001]].map(([name,label,min])=><label key={name}>{label}<input type="number" min={min} max={name==='horizon_s'?3600:undefined} step="any" required value={options[name]??''} onChange={e=>option(name,e.target.value)}/></label>)}
          </div><label className="check-label"><input type="checkbox" checked={custom} onChange={e=>setCustom(e.target.checked)}/>Ubah fitur prediksi pada skenario sintetis</label>
          {custom&&scenario&&<details open className="feature-editor"><summary>Fitur prediksi · horizon 24 jam</summary><p className="muted">Contoh sintetis, bukan data BBWS. Fase tanaman diwakili Kc asumsi. Volume turunan = luas × kedalaman air.</p>
            <Table heads={['Zona','Luas (m²)','Kc','ET0 (mm/hari)','Hujan efektif (mm)','Perkolasi (mm/hari)']}>{scenario.zones.map(z=><tr key={z.zone_id}><td>{z.zone_id}</td>{['area_m2','kc_assumed','et0_forecast_mm','effective_rain_forecast_mm','percolation_assumed_mm'].map(name=><td key={name}><input aria-label={`${z.zone_id} ${name}`} type="number" min={name==='area_m2'?0.001:0} step="any" required value={z.features[name]??''} onChange={e=>feature(z.zone_id,name,e.target.value)}/></td>)}</tr>)}</Table>
            <div className="integration-form">{[['previous_inflow_L','Pasokan sebelumnya (L/24 jam)'],['base_inflow_assumed_L','Aliran dasar asumsi (L/24 jam)'],['planned_upstream_inflow_L','Rencana hulu (L/24 jam)'],['rain_forecast_mm','Prakiraan hujan sumber (mm)']].map(([name,label])=><label key={name}>{label}<input type="number" min="0" step="any" required value={scenario.supply[name]??''} onChange={e=>feature('supply',name,e.target.value)}/></label>)}</div></details>}
          <button type="submit" className="primary-button">{busy?'Menghitung...':'Hitung ML → LP → jadwal'}</button></fieldset></form><p className="integration-caption">1 liter skenario × 1000 × 0,00001 = 0,01 mL demo. Pemetaan ini belum membuktikan kesebangunan hidraulik.</p></section>{metrics}{allocation}
          {plan&&<section className="panel"><h3>Asal prediksi</h3><p className="muted">Angka dalam liter per 24 jam, sebelum konversi ke mL demo.</p><Table heads={['Target','Model ML','Prediksi ML (L)','Baseline (L)','Metode dipakai']}>{plan.predictions.map(p=><tr key={p.task+p.zone_id}><td>{p.task==='supply'?'Pasokan bersama':`Kebutuhan ${p.zone_id}`}</td><td>{p.selected_ml_method}</td><td>{num(p.ml_prediction_L)}</td><td>{num(p.baseline_prediction_L)}</td><td>{p.method_used}</td></tr>)}</Table>
            <div className="notice">Model ML masih percobaan; belum terbukti selalu lebih baik daripada baseline. {plan.warnings.includes('ML_FEATURE_OUTSIDE_TRAINING_RANGE')?'Ada fitur di luar rentang training; perlu review.':'Lihat peringatan pada JSON.'}</div><Json value={plan}/></section>}
        </>}
        {menu==='zones'&&<section className="panel"><h3>Skema satu sumber, tiga zona</h3><p className="muted">Skema konseptual, bukan peta geografis atau model hidraulik jaringan BBWS.</p><div className="network-source">Sumber bersama · {num(plan?.source_budget.available_demo_ml)} mL demo</div><div className="integration-zone-grid">{'ABC'.split('').map(id=>{const z=zones.find(z=>z.zone_id===id);return <article className={`zone-summary-card zone-card-${id}`} key={id}><h3>Zona {id} · {positions[id]}</h3><p>Kebutuhan: {num(z?.need_ml)} mL</p><p>Release: {num(z?.release_ml)} mL</p><p>Perkiraan diterima: {num(z?.expected_delivered_ml)} mL</p><strong>Pemenuhan: {num(z?percent(z.fulfillment_ratio):null)}%</strong></article>})}</div><p className="integration-caption">Budget dihitung satu kali. Debit dari beberapa titik kanal tidak dijumlahkan sebagai sumber baru.</p><button className="secondary-button" onClick={()=>setMenu('analytics')}>Ubah skenario di Analitik AI</button></section>}
        {menu==='schedule'&&<>{plan?<Schedule schedule={plan.schedule}/>:<div className="panel empty">Hitung rencana di Analitik AI untuk melihat jadwal alokasi saat ini.</div>}
          <section className="panel"><h3>Uji reoptimasi adaptif</h3><p className="muted">Eksperimen terpisah memakai replay sintetis bawaan, pasokan dan horizon dari form Analitik AI. Fitur khusus, eta, debit, serta kapasitas form tidak diteruskan ke replay ini.</p><form onSubmit={e=>{e.preventDefault();runReplay()}} className="replay-form"><label>Kekurangan paket pertama (%)<input type="number" min="0" max="100" step="any" required value={shortfall} onChange={e=>setShortfall(e.target.value)}/></label><button className="primary-button" disabled={busy}>{busy?'Menghitung...':'Jalankan replay simulasi'}</button></form><p className="integration-caption">Pemicu: deviasi absolut lebih dari 10% pada dua pembacaan paket yang sama. Tepat 10% tidak memicu. Ini bukan validasi derau sensor.</p>{replayError&&<div role="alert" className="integration-error">{replayError}</div>}</section>
          {replay&&<section className="panel"><h3>{replay.child_plan_created?'Rencana pengganti berhasil dihitung':'Tidak ada rencana pengganti pada percobaan ini'}</h3><div className="summary-grid integration-metrics"><Metric label="Disparitas statis" value={`${num(percent(replay.static_comparison.static.disparity_ratio))} pp`} note="Setelah gangguan simulasi"/><Metric label="Disparitas adaptif" value={`${num(percent(replay.static_comparison.adaptive.disparity_ratio))} pp`} note="Rasio pemenuhan satu siklus"/><Metric label="Neraca sumber" value={replay.ledger.source_balance_ok?'Konsisten':'Perlu review'} note="Neraca volume simulator"/></div><p>Belum membuktikan peningkatan lapangan atau target reoptimasi kurang dari 30 detik.</p><Json value={replay}/></section>}
        </>}
        {menu==='monitoring'&&<><DevicePanel plan={plan} role={role}/><section className="panel"><div className="panel-heading"><div><h3>Telemetri masuk</h3><p className="muted">Pembaruan setiap 5 detik · 50 catatan terakhir.</p></div><button className="secondary-button" onClick={()=>monitor()} disabled={refreshing}>Perbarui data</button></div><div className="notice">{role==='judge'?'Data contoh ditandai SIMULASI dan tidak berasal dari sensor fisik.':'SIMULASI dan MINIATUR ditandai terpisah. Format lama tanpa satuan jelas disimpan mentah; debit tidak dianggap volume.'}</div><button className="primary-button" onClick={()=>monitor(true)} disabled={refreshing}>Kirim telemetri SIMULASI</button>
          {telemetry.length===0?<p className="empty">{role==='judge'?'Belum ada data simulasi. Klik Kirim telemetri SIMULASI untuk membuat contoh.':'Belum ada data diterima. Hubungkan pengirim IoT atau gunakan tombol simulasi.'}</p>:<Table heads={['Perangkat / zona','Asal / kualitas','Pembacaan','Observasi / kesegaran']}>{telemetry.map(r=><tr key={`${r.format||'canonical'}-${r.device_id}-${r.record_id}`}><td>{r.device_id}<br/>{r.zone_id||'Format lama'}</td><td><strong>{r.context}</strong><br/>{r.quality}<br/>{r.value_kind}</td><td>{r.format==='LEGACY_RAW'?<><strong>Satuan belum dikonfirmasi</strong>{['water_distance','flow_a','flow_b','flow_c'].map(k=><div key={k}>{k}: {num(r[k])}</div>)}</>:Object.entries(r.readings).map(([k,v])=><div key={k}>{k}: {num(v)} {k.endsWith('_ml_s')?'mL/s':k.endsWith('_ml')?'mL':'cm'}</div>)}</td><td>{time(r.observed_at)}<br/>{r.stale===null?'Waktu observasi belum dikonfirmasi':r.stale?`STALE · ${num(r.age_s)} s`:`Baru · ${num(r.age_s)} s`}<br/><small>Diterima {time(r.received_at)}</small></td></tr>)}</Table>}
          <p className="integration-caption">{role==='judge'?'Telemetri simulasi tidak digunakan untuk training atau mengubah rencana secara otomatis.':'Belum digunakan untuk training atau reoptimasi. VALID adalah deklarasi pengirim dengan referensi kalibrasi, bukan pengesahan pengukuran oleh server.'}</p></section></>}
        {menu==='history'&&<section className="panel"><h3>Riwayat rencana tersimpan</h3><p className="muted">Riwayat perhitungan AI dalam SQLite; bukan rekap kekurangan lapangan atau histori defisit antarsiklus untuk LP.</p>{history.length===0?<p className="empty">Belum ada rencana tersimpan.</p>:<Table heads={['Waktu','ID rencana','Budget','Release','Disparitas','Status']}>{history.map(h=><tr key={h.plan_id}><td>{time(h.created_at)}</td><td><code>{h.plan_id.slice(0,14)}…</code></td><td>{num(h.budget_ml)} mL</td><td>{num(h.release_ml)} mL</td><td>{num(h.disparity_pp)} pp</td><td>{h.status}<br/>{h.context}</td></tr>)}</Table>}</section>}
        {menu==='settings'&&<section className="panel"><h3>Informasi integrasi</h3><dl className="integration-dl"><dt>Frontend</dt><dd>React / Vite · dasbor dan API pada satu alamat saat deployment</dd><dt>Backend</dt><dd>Flask / Waitress · penyimpanan SQLite persisten pada hosting</dd><dt>Model</dt><dd>Demand dan loss: Gradient Boosting. Supply: Ridge. Kandidat lain belum dipromosikan.</dd><dt>Optimasi</dt><dd>LP: kebutuhan, satu budget sumber, efisiensi, kapasitas, pengaman hilir. Eta dibekukan sebelum solve.</dd><dt>Scheduler</dt><dd>Volume ÷ debit; setup 1 s, delay A/B/C 2/4/6 s masih asumsi.</dd><dt>IoT</dt><dd>Prototipe perangkat didemonstrasikan terpisah. Monitoring publik menggunakan telemetri simulasi; tidak mengendalikan pompa atau pintu fisik.</dd><dt>Status</dt><dd>TRAINED_SYNTHETIC_ONLY · 0 baris pengukuran nyata pada training · execution_allowed=false.</dd></dl><div className="notice">Tahap pengembangan berikutnya mencakup validasi dengan data pengukuran dan integrasi perangkat. Hasil simulasi tidak menyatakan akurasi lapangan.</div></section>}
        <footer className="integration-footer">BALANCE · Prototipe Tahap 2 · Prediksi dan optimasi irigasi · Data simulasi</footer>
      </section>
    </main>
  </div>
}
export default App
