// Interactive DOM test against a real Flask/ML/LP process on a separate port.
// JSDOM does not validate browser layout or physical hardware.
import { spawn, spawnSync } from 'node:child_process'
import { readFileSync, cpSync, mkdtempSync, rmSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import process from 'node:process'
import { beforeAll, beforeEach, afterAll, afterEach, expect, test } from 'vitest'
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from './App.jsx'

let child, state, config, testPython, cookie=''
const nativeFetch=globalThis.fetch
const base='http://127.0.0.1:5015'
const seen=[]
afterEach(()=>cleanup())
beforeEach(()=>{
  cookie='';seen.length=0
  const result=spawnSync(testPython,['-c',"import sqlite3,sys;db=sqlite3.connect(sys.argv[1]);[db.execute('DELETE FROM '+t) for t in ['iot_events','iot_commands','iot_devices','records']];db.commit();db.close()",resolve(state,'integration.sqlite3')],{env:process.env})
  if(result.status!==0)throw new Error('Temporary test database reset failed')
})
beforeAll(async()=>{
  const backend=resolve('../backend')
  config=JSON.parse(readFileSync(resolve(backend,'config.local.json'),'utf8'))
  state=mkdtempSync(resolve(tmpdir(),'balance-ui-'))
  cpSync(resolve(backend,'.state'),state,{recursive:true})
  rmSync(resolve(state,'integration.sqlite3'),{force:true})
  const venvPython=resolve('..','.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python')
  testPython=process.env.BALANCE_TEST_PYTHON||(existsSync(venvPython)?venvPython:'python')
  child=spawn(testPython,['-c',
    'import sys;from app import create_app;from waitress import serve;serve(create_app(sys.argv[1]),host="127.0.0.1",port=5015)',state],{cwd:backend,env:process.env,stdio:'pipe'})
  let logs='';child.stderr.on('data',d=>{logs+=d.toString()})
  for(let i=0;i<100;i++){
    try{const r=await nativeFetch(base+'/api/status');if(r.ok)break}catch{/* Starting */}
    if(child.exitCode!==null)throw new Error('Backend test process failed: '+logs)
    await new Promise(r=>setTimeout(r,100))
  }
  globalThis.fetch=async(path,options={})=>{
    const url=String(path).startsWith('/')?base+path:String(path)
    const r=await nativeFetch(url,{...options,headers:{...options.headers,...(cookie?{Cookie:cookie}:{})}})
    const set=r.headers.get('set-cookie');if(set)cookie=set.split(';')[0]
    if(options.body)seen.push({path:String(path),status:r.status,body:JSON.parse(options.body),result:await r.clone().json()})
    return r
  }
  globalThis.ResizeObserver=class{observe(){} unobserve(){} disconnect(){}}
},30000)
afterAll(async()=>{
  cleanup();globalThis.fetch=nativeFetch
  if(child&&child.exitCode===null&&child.signalCode===null){const exited=new Promise(r=>child.once('exit',r));child.kill();await exited}
  if(state)rmSync(state,{recursive:true,force:true})
})

test('judge username/password can use simulation but cannot access miniature commands',async()=>{
  const user=userEvent.setup();render(<App/>);await screen.findByText('Backend: READY')
  expect(screen.getByLabelText('Username').value).toBe('juri')
  fireEvent.change(screen.getByLabelText('Username'),{target:{value:config.judge_username}})
  fireEvent.change(screen.getByLabelText('Password'),{target:{value:config.judge_password}})
  await user.click(screen.getByRole('button',{name:'Masuk dasbor'}))
  await screen.findByRole('heading',{name:'Dashboard BALANCE',exact:true})
  await user.click(screen.getByRole('button',{name:/Monitoring/}))
  await screen.findByRole('heading',{name:'Demo juri'})
  expect(screen.queryByRole('button',{name:'Kirim rencana DRY_RUN'})).toBeNull()
  const denied=await globalThis.fetch('/api/iot/v2/commands',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})
  expect(denied.status).toBe(403)
  await user.click(screen.getByRole('button',{name:'Keluar'}))
  await screen.findByRole('heading',{name:'Masuk ke dasbor'})
})

test('operator queues a dry-run once and displays device completion from real API',async()=>{
  const user=userEvent.setup();render(<App/>);await screen.findByText('Backend: READY')
  await user.click(screen.getByRole('button',{name:'Gunakan kunci operator'}))
  fireEvent.change(screen.getByLabelText('Kunci dasbor'),{target:{value:config.dashboard_key}})
  await user.click(screen.getByRole('button',{name:'Masuk dasbor'}))
  await screen.findByRole('heading',{name:'Dashboard BALANCE',exact:true})
  await waitFor(()=>expect(screen.getByRole('button',{name:'Hitung rekomendasi',exact:true}).disabled).toBe(false))
  await user.click(screen.getByRole('button',{name:'Hitung rekomendasi',exact:true}))
  await screen.findByRole('heading',{name:'Rekomendasi pembagian air'})
  const send=async(path,body)=>{
    const r=await nativeFetch(base+'/api/iot/v2/device'+path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json','X-Balance-Key':config.device_keys['esp32-demo']},body:body===undefined?undefined:JSON.stringify(body)})
    expect(r.status).toBe(200);return r.json()
  }
  await send('/hello',{device_id:'esp32-demo',device_kind:'SIMULATOR',boot_id:'ui-boot',firmware_version:'balance-iot/2.0',armed:false,pulses_per_liter:5880,calibration_ref:''})
  await user.click(screen.getByRole('button',{name:/Monitoring/}))
  await screen.findByRole('heading',{name:'Sambungan miniatur ESP32'})
  await user.click(screen.getByRole('button',{name:'Kirim rencana DRY_RUN'}))
  await screen.findByText('QUEUED',{exact:true})
  const job=(await send('/next-command?boot_id=ui-boot')).command
  const claim=await send('/commands/'+job.command_id+'/claim',{boot_id:'ui-boot'})
  const values={A:0,B:0,C:0}
  const event=async(type,seq)=>send('/commands/'+job.command_id+'/events',{event_id:'ui-event-'+seq,boot_id:'ui-boot',lease_token:claim.lease_token,type,released_ml:values})
  await event('STARTED',1)
  job.slots.forEach(s=>{values[s.zone_id]=s.target_release_ml})
  await event('COMPLETED',2)
  await screen.findByText('COMPLETED',{exact:true},{timeout:6000})
  expect((await send('/next-command?boot_id=ui-boot')).command).toBeNull()
  await user.click(screen.getByRole('button',{name:'Keluar'}))
})

test('login, real plan, custom form, failure clearing, replay boundary, telemetry and all menus',async()=>{
  const user=userEvent.setup()
  render(<App/>);await screen.findByText('Backend: READY')
  await user.click(screen.getByRole('button',{name:'Gunakan kunci operator'}))
  fireEvent.change(screen.getByLabelText('Kunci dasbor'),{target:{value:config.dashboard_key}})
  await user.click(screen.getByRole('button',{name:'Masuk dasbor'}))
  await screen.findByRole('heading',{name:'Dashboard BALANCE',exact:true})
  await waitFor(()=>expect(screen.getByRole('button',{name:'Hitung rekomendasi',exact:true}).disabled).toBe(false))
  await user.click(screen.getByRole('button',{name:'Hitung rekomendasi',exact:true}))
  await screen.findByRole('heading',{name:'Rekomendasi pembagian air'})
  let last=seen.findLast(x=>x.path==='/api/plan')
  expect(last.status).toBe(200);expect(last.result.source_budget.available_demo_ml).toBeCloseTo(908.284,2)
  expect(screen.getAllByText('908,28 mL',{exact:true}).length).toBe(2)
  const nav=async text=>user.click(screen.getByRole('button',{name:new RegExp(text)}))
  await nav('Analitik AI')
  await user.selectOptions(screen.getByLabelText('Metode prediksi'),'baseline')
  await user.selectOptions(screen.getByLabelText('Sumber eta'),'assumed')
  await user.click(screen.getByLabelText('Ubah fitur prediksi pada skenario sintetis'))
  for(const [name,value] of [['area_m2','5000'],['kc_assumed','1'],['et0_forecast_mm','4'],['effective_rain_forecast_mm','1'],['percolation_assumed_mm','4']])
    fireEvent.change(screen.getByLabelText('A '+name,{exact:true}),{target:{value}})
  await user.click(screen.getByRole('button',{name:'Hitung ML → LP → jadwal',exact:true}))
  await screen.findByRole('heading',{name:'Rekomendasi pembagian air'})
  last=seen.findLast(x=>x.path==='/api/plan')
  expect(last.status).toBe(200)
  expect(last.result.predictions.find(p=>p.zone_id==='A').value_used_L).toBe(35000)
  expect(last.result.eta.inference_used).toBe(false)
  await user.click(screen.getByLabelText('Ubah fitur prediksi pada skenario sintetis'))
  await user.selectOptions(screen.getByLabelText('Metode prediksi'),'ml')
  await user.selectOptions(screen.getByLabelText('Sumber eta'),'learned')
  fireEvent.change(screen.getByLabelText('Pasokan simulasi (%)'),{target:{value:'0'}})
  await user.click(screen.getByRole('button',{name:'Hitung ML → LP → jadwal',exact:true}))
  await screen.findByText('Tidak ada pasokan. Disparitas nol tidak menunjukkan kebutuhan terpenuhi.')
  fireEvent.change(screen.getByLabelText('Pasokan simulasi (%)'),{target:{value:'100'}})
  fireEvent.change(screen.getByLabelText('Horizon jadwal (detik)'),{target:{value:'1'}})
  await user.click(screen.getByRole('button',{name:'Hitung ML → LP → jadwal',exact:true}))
  await screen.findByRole('alert')
  expect(screen.queryByRole('heading',{name:'Rekomendasi pembagian air'})).toBeNull()
  expect(seen.findLast(x=>x.path==='/api/plan').status).toBe(422)
  fireEvent.change(screen.getByLabelText('Horizon jadwal (detik)'),{target:{value:'600'}})
  await user.click(screen.getByRole('button',{name:'Hitung ML → LP → jadwal',exact:true}))
  await screen.findByRole('heading',{name:'Rekomendasi pembagian air'})
  await nav('Jadwal Irigasi')
  await user.click(screen.getByRole('button',{name:'Jalankan replay simulasi'}))
  await screen.findByRole('heading',{name:'Rencana pengganti berhasil dihitung'})
  expect(seen.findLast(x=>x.path==='/api/scheduler/simulate').result.ledger.source_balance_ok).toBe(true)
  fireEvent.change(screen.getByLabelText('Kekurangan paket pertama (%)'),{target:{value:'10'}})
  await user.click(screen.getByRole('button',{name:'Jalankan replay simulasi'}))
  await screen.findByRole('heading',{name:'Tidak ada rencana pengganti pada percobaan ini'})
  await nav('Monitoring')
  await user.click(screen.getByRole('button',{name:'Kirim telemetri SIMULASI'}))
  await screen.findAllByText('flow_ml_s: 6 mL/s',{exact:true})
  const legacy={record_id:'ui-test-raw',device_id:'esp32-demo',water_distance:12,flow_a:0,flow_b:1,flow_c:2}
  const r=await nativeFetch(base+'/api/iot/data',{method:'POST',headers:{'Content-Type':'application/json','X-Balance-Key':config.device_keys['esp32-demo']},body:JSON.stringify(legacy)})
  expect(r.status).toBe(201)
  await user.click(screen.getByRole('button',{name:'Perbarui data'}))
  await screen.findByText('Satuan belum dikonfirmasi',{exact:true})
  expect(screen.getByText('flow_a: 0',{exact:true})).toBeTruthy()
  await nav('Riwayat');await screen.findByRole('heading',{name:'Riwayat rencana tersimpan'})
  expect(screen.getAllByRole('row').length).toBe(5)
  await nav('Peta Zona');await screen.findByRole('heading',{name:'Skema satu sumber, tiga zona'})
  await nav('Pengaturan');await screen.findByRole('heading',{name:'Informasi integrasi'})
  await nav('Dashboard');await screen.findByRole('heading',{name:'Rekomendasi pembagian air'})
  await waitFor(()=>expect(screen.getByRole('button',{name:'Hitung rekomendasi',exact:true}).disabled).toBe(false))
  await user.click(screen.getByRole('button',{name:'Keluar'}))
  await screen.findByRole('heading',{name:'Masuk ke dasbor'})
})
