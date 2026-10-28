import tempfile, unittest, threading
import queue
from pathlib import Path
from unittest.mock import patch, Mock
from types import SimpleNamespace
import tkinter as tk
import main
from desktop_ui import (MouseWheelRouter, CopyTextRouter, SelectableText,
                        SelectableLabel, SelectableCheckbutton, SelectableRadiobutton)
from summary_queue import SummaryQueueStore

class QueueTests(unittest.TestCase):
 def test_new_meeting_removes_two_old_terminal_records_only(self):
  with tempfile.TemporaryDirectory() as directory:
   store=SummaryQueueStore(Path(directory)/'queue.json')
   store.set_page_capacity(4)
   ids=[]
   for name,status in [('old_done','completed'),('failed','failed'),('old_silent','skipped'),('running','running')]:
    path=Path(directory)/(name+'.txt'); path.write_text('Keep this file',encoding='utf-8')
    record,_=store.add(path,'text',name,{})
    store.update(record['id'],status=status)
    ids.append(record['id'])
   store.add(Path(directory)/'new.txt','new text','new',{})
   self.assertEqual([r['file_stem'] for r in store.snapshot()],['failed','running','new'])
   self.assertEqual(store.pending_count,3)
   self.assertTrue((Path(directory)/'old_done.txt').exists())
   self.assertTrue((Path(directory)/'old_silent.txt').exists())
   self.assertEqual(len(SummaryQueueStore(store.path).snapshot()),3)
 def test_full_page_never_removes_unfinished_meetings(self):
  with tempfile.TemporaryDirectory() as directory:
   store=SummaryQueueStore(Path(directory)/'queue.json'); store.set_page_capacity(1)
   for name in ('first','second','third'):
    store.add(Path(directory)/(name+'.txt'),'text',name,{})
   self.assertEqual(len(store.snapshot()),3)
 def test_startup_resource_pause_is_not_a_server_failure(self):
  app=main.MeetingRecorderApp.__new__(main.MeetingRecorderApp)
  app.llm_health_ok=Mock(return_value=False)
  app.llm_process=Mock(); app.llm_process.poll.return_value=None
  app.llm_process_lock=threading.Lock(); app.shutting_down=False
  app.summary_should_pause=Mock(return_value=True)
  app.summary_pause_reason='Waiting for available memory'
  app.stop_managed_llm_server=Mock()
  with self.assertRaises(main.SummaryPaused):
   app.ensure_managed_llm_server(main.default_realtime_settings())
  app.stop_managed_llm_server.assert_called_once()
 def test_resource_pause_resumes_without_marking_meeting_failed(self):
  with tempfile.TemporaryDirectory() as directory:
   app=self.make_app(directory)
   settings=main.default_realtime_settings()
   record,_=app.summary_store.add(Path(directory)/'meeting.txt','transcript','meeting',settings)
   app.realtime_settings=settings
   app.call_local_llm_summary=Mock(side_effect=[main.SummaryPaused('memory pressure'),'Completed minutes'])
   app.sanitize_llm_summary=lambda text, transcript:text
   app.split_transcript_for_summary=Mock(return_value=['transcript'])
   app.run_background_summary_job(app.summary_job_from_record(record))
   self.assertEqual(app.call_local_llm_summary.call_count,2)
   self.assertEqual(app.summary_store.snapshot()[0]['status'],'completed')
   self.assertEqual(app.summary_store.pending_count,0)
 def test_recording_waits_visibly_then_uses_final_transcript(self):
  with tempfile.TemporaryDirectory() as directory:
   app=self.make_app(directory)
   app.realtime_settings=main.default_realtime_settings()
   app.start_summary_worker=Mock()
   session=main.RecordingSession('meeting',text_final_path=Path(directory)/'meeting.txt')
   session.transcript_text='partial text'
   app.register_pending_transcript(session)
   record=app.summary_store.snapshot()[0]
   self.assertEqual((record['status'],record['progress']),('transcribing',0))
   self.assertEqual(app.summary_store.pending_count,1)
   self.assertTrue(app.summary_jobs.empty())
   app.start_summary_worker.assert_not_called()
   app.queue_background_summary(session.text_final_path,'complete final text','meeting')
   records=app.summary_store.snapshot()
   self.assertEqual(len(records),1)
   self.assertEqual(records[0]['id'],record['id'])
   self.assertEqual(records[0]['status'],'queued')
   self.assertEqual(app.summary_jobs.get_nowait().transcript,'complete final text')
   app.start_summary_worker.assert_called_once()
 def test_empty_final_transcript_does_not_stay_pending(self):
  with tempfile.TemporaryDirectory() as directory:
   store=SummaryQueueStore(Path(directory)/'queue.json')
   path=Path(directory)/'meeting.txt'
   store.add(path,'','meeting',{},awaiting_transcript=True)
   record=store.finish_transcript(path,'')
   self.assertEqual(record['status'],'skipped')
   self.assertEqual(store.pending_count,0)
   self.assertEqual(SummaryQueueStore(store.path).snapshot()[0]['status'],'skipped')
 def test_restart_never_summarizes_unfinished_transcript(self):
  with tempfile.TemporaryDirectory() as directory:
   store=SummaryQueueStore(Path(directory)/'queue.json')
   store.add(Path(directory)/'meeting.txt','partial','meeting',{},awaiting_transcript=True)
   restored=SummaryQueueStore(store.path).snapshot()[0]
   self.assertEqual(restored['status'],'failed')
   self.assertTrue(restored['awaiting_transcript'])
 def make_app(self, directory):
  app=main.MeetingRecorderApp.__new__(main.MeetingRecorderApp)
  app.summary_store=SummaryQueueStore(Path(directory)/'queue.json')
  app.shutting_down=False; app.language='en'; app.offline_window=None
  app.summary_jobs=queue.Queue(); app.summary_lock=threading.Lock()
  app.update_tray_menu=Mock(); app.stop_managed_llm_server=Mock()
  app.root=Mock(); app.wait_for_summary_job=Mock()
  return app
 def test_one_failure_does_not_block_next_meeting(self):
  with tempfile.TemporaryDirectory() as directory:
   app=self.make_app(directory)
   for name in ('first','second'):
    record,_=app.summary_store.add(Path(directory)/(name+'.txt'),'transcript',name,{'llm_summary_enabled':True})
    app.summary_jobs.put(app.summary_job_from_record(record))
   app.summary_jobs.put(None)
   app.build_local_llm_meeting_summary=Mock(side_effect=[RuntimeError('model failed'),'Completed minutes'])
   app.background_summary_worker()
   self.assertEqual([r['status'] for r in app.summary_store.snapshot()],['failed','completed'])
   self.assertEqual(app.summary_store.pending_count,1)
   self.assertIn('Completed minutes',(Path(directory)/'second.txt').read_text(encoding='utf-8'))
 def test_write_failure_never_reaches_100(self):
  with tempfile.TemporaryDirectory() as directory:
   app=self.make_app(directory)
   record,_=app.summary_store.add(Path(directory)/'meeting.txt','text','meeting',{'llm_summary_enabled':True})
   app.build_local_llm_meeting_summary=Mock(return_value='minutes')
   app.write_background_summary=Mock(return_value=False)
   app.run_background_summary_job(app.summary_job_from_record(record))
   self.assertEqual(app.summary_store.snapshot()[0]['status'],'failed')
   self.assertLess(app.summary_store.snapshot()[0]['progress'],100)
 def test_waiting_for_resources_stays_zero(self):
  with tempfile.TemporaryDirectory() as directory:
   app=self.make_app(directory)
   record,_=app.summary_store.add(Path(directory)/'meeting.txt','text','meeting',{})
   job=app.summary_job_from_record(record)
   app.summary_should_pause=Mock(side_effect=[True,False])
   def observe_pause(_):
    current=app.summary_store.snapshot()[0]
    self.assertEqual(current['status'],'waiting')
    self.assertEqual(current['progress'],0)
    self.assertRegex(current['detail'],r'Next check: \d{2}:\d{2}:\d{2} \(2s\)')
    self.assertIsNotNone(current['next_check_at'])
   with patch('main.time.sleep',side_effect=observe_pause):
    main.MeetingRecorderApp.wait_for_summary_job(app,job)
   app.stop_managed_llm_server.assert_called_once()
   self.assertIsNone(app.summary_store.snapshot()[0]['next_check_at'])
 def test_restore_pending_and_checkpoints_and_deduplicate(self):
  with tempfile.TemporaryDirectory() as directory:
   path=Path(directory)/'queue.json'; store=SummaryQueueStore(path)
   record,added=store.add(Path(directory)/'meeting.txt','transcript','meeting',{})
   self.assertTrue(added)
   self.assertFalse(store.add(Path(directory)/'meeting.txt','transcript','meeting',{})[1])
   store.update(record['id'],status='running',progress=25,chunks=['first'])
   restored=SummaryQueueStore(path)
   self.assertEqual(restored.snapshot()[0]['status'],'queued')
   self.assertEqual(restored.snapshot()[0]['chunks'],['first'])
   self.assertEqual(restored.pending_count,1)
   restored.update(record['id'],status='completed',progress=100)
   self.assertEqual(restored.pending_count,0)
 def test_real_chunk_progress_and_resume(self):
  app=main.MeetingRecorderApp.__new__(main.MeetingRecorderApp)
  app.realtime_settings=main.default_realtime_settings(); app.shutting_down=False; app.language='en'
  app.split_transcript_for_summary=Mock(return_value=['one','two','three'])
  app.call_local_llm_summary=Mock(side_effect=['summary two','summary three','merged summary'])
  app.sanitize_llm_summary=lambda text, transcript:text
  app.write_summary_debug_file=Mock()
  cached=['Chunk 1:\nsummary one']; progress=[]
  result=app.build_local_llm_meeting_summary('Transcript',completed_chunks=cached,progress_callback=lambda *args:progress.append(args))
  self.assertIn('merged summary',result)
  self.assertEqual(app.call_local_llm_summary.call_count,3)
  self.assertEqual(len(cached),3)
  self.assertEqual(progress[0][:2],(1,4))
  self.assertIn((3,4,'Merging final meeting minutes'),progress)
 def test_failed_job_does_not_disappear(self):
  with tempfile.TemporaryDirectory() as directory:
   store=SummaryQueueStore(Path(directory)/'queue.json')
   record,_=store.add(Path(directory)/'meeting.txt','transcript','meeting',{})
   store.update(record['id'],status='failed',detail='download unavailable')
   self.assertEqual(SummaryQueueStore(store.path).snapshot()[0]['status'],'failed')
   self.assertEqual(store.pending_count,1)

class DesktopTests(unittest.TestCase):
 def test_all_page_labels_keep_variable_updates_and_controls_work(self):
  root=tk.Tk(); root.withdraw()
  try:
   value=tk.StringVar(value='Idle')
   label=SelectableLabel(root,textvariable=value)
   value.set('Waiting for resources')
   self.assertEqual(label.get('1.0','end-1c'),'Waiting for resources')
   label.tag_add('sel','1.0','1.7')
   value.set('Waiting for resources')
   self.assertEqual(label.get('sel.first','sel.last'),'Waiting')
   label.configure(text='Completed')
   self.assertEqual(label.get('1.0','end-1c'),'Completed')
   selected=tk.BooleanVar(value=False)
   checkbox=SelectableCheckbutton(root,text='Managed LLM server',variable=selected)
   checkbox.invoke()
   self.assertTrue(selected.get())
   self.assertEqual(checkbox.caption.cget('state'),'disabled')
   language=tk.StringVar(value='en')
   radio=SelectableRadiobutton(root,text='中文',variable=language,value='zh')
   radio.invoke()
   self.assertEqual(language.get(),'zh')
  finally:
   for callback in root.tk.splitlist(root.tk.call('after','info')): root.tk.call('after','cancel',callback)
   root.destroy()
 def test_queue_text_supports_left_drag_and_survives_refresh(self):
  root=tk.Tk()
  try:
   root.geometry('500x200')
   field=SelectableText(root,text='Waiting for transcription — 0%')
   field.pack(fill='x'); root.update()
   x,y,width,height=field.bbox('1.0')
   end=field.bbox('1.7')[0]
   field.event_generate('<ButtonPress-1>',x=x+1,y=y+height//2)
   field.event_generate('<B1-Motion>',x=end+1,y=y+height//2,state=256)
   field.event_generate('<ButtonRelease-1>',x=end+1,y=y+height//2)
   root.update()
   self.assertEqual(field.get('sel.first','sel.last'),'Waiting ')
   field.set_text('Waiting for transcription — 0%')
   self.assertEqual(field.get('sel.first','sel.last'),'Waiting ')
   field.insert('1.0','must not edit')
   self.assertEqual(field.get('1.0','end-1c'),'Waiting for transcription — 0%')
  finally:
   for callback in root.tk.splitlist(root.tk.call('after','info')): root.tk.call('after','cancel',callback)
   root.destroy()
 def test_copy_dynamic_status_and_disabled_text_selection(self):
  root=tk.Tk(); root.withdraw()
  try:
   router=CopyTextRouter(root)
   value=tk.StringVar(value='Waiting for resources, 0%')
   label=main.ttk.Label(root,textvariable=value)
   value.set('Completed, 100%')
   event=SimpleNamespace(widget=label,x_root=0,y_root=0)
   with patch.object(router.menu,'tk_popup'), patch.object(router.menu,'grab_release'), patch.object(root,'clipboard_clear'), patch.object(root,'clipboard_append') as append:
    self.assertEqual(router.popup(event),'break')
    router.menu.invoke(0)
    append.assert_called_once_with('Completed, 100%')
   field=tk.Text(root); field.insert('1.0','Meeting summary')
   field.tag_add('sel','1.0','1.7'); field.configure(state='disabled')
   self.assertEqual(router.text_values(field),('Meeting','Meeting summary'))
   entry=main.ttk.Entry(root); entry.insert(0,'Model path'); entry.selection_range(0,5)
   self.assertEqual(router.text_values(entry),('Model','Model path'))
  finally: root.destroy()
 def test_wheel_over_controls_scrolls_page_and_text_only_once(self):
  root=tk.Tk()
  try:
   root.geometry('400x300')
   router=MouseWheelRouter(root)
   canvas=tk.Canvas(root); canvas.pack(fill='both',expand=True)
   content=tk.Frame(canvas); canvas.create_window((0,0),window=content,anchor='nw')
   entry=tk.Entry(content); entry.pack(); entry.insert(0,'unchanged')
   text=tk.Text(content,height=4); text.pack()
   text.insert('1.0','\n'.join(str(i) for i in range(100)))
   for _ in range(40): tk.Label(content,text='Settings row').pack()
   root.update()
   canvas.configure(scrollregion=canvas.bbox('all'))
   with patch.object(root,'winfo_containing',return_value=entry):
    entry.event_generate('<MouseWheel>',delta=-120); root.update()
   self.assertGreater(canvas.yview()[0],0)
   self.assertEqual(entry.get(),'unchanged')
   canvas.yview_moveto(0); text.yview_moveto(0); root.update()
   before=canvas.yview()
   with patch.object(root,'winfo_containing',return_value=text):
    text.event_generate('<MouseWheel>',delta=-120); root.update()
   self.assertEqual(text.index('@0,0'),'4.0')
   self.assertEqual(canvas.yview(),before)
  finally:
   for callback in root.tk.splitlist(root.tk.call('after','info')): root.tk.call('after','cancel',callback)
   root.destroy()
 def test_six_tabs_and_navigation_and_hide_to_tray(self):
  with tempfile.TemporaryDirectory() as directory:
   root=tk.Tk(); root.withdraw()
   try:
    with patch('main.output_directory',return_value=Path(directory)), patch.object(main.MeetingRecorderApp,'load_user_settings'), patch.object(main.MeetingRecorderApp,'install_windows_power_shutdown_hooks'), patch('main.pystray.Icon'):
     app=main.MeetingRecorderApp(root)
     app.show_main_tab('queue'); root.update_idletasks()
     labels=[app.main_notebook.tab(tab,'text') for tab in app.main_notebook.tabs()]
     self.assertEqual(labels,['Recording & Text','Summary Queue','Offline Transcript','System Settings','Screenshot','About'])
     self.assertEqual(app.main_notebook.select(),str(app.main_tabs['queue']))
     record,_=app.summary_store.add(Path(directory)/'meeting.txt','text','meeting',{})
     app.summary_store.update(record['id'],status='running',progress=25,detail='Section 1/3')
     app.queue_view.refresh()
     self.assertEqual(float(app.queue_view.rows[record['id']][1]['value']),25)
     newest,_=app.summary_store.add(Path(directory)/'new.txt','new','new meeting',{})
     app.queue_view.refresh()
     self.assertEqual(app.queue_view.content.pack_slaves()[0],app.queue_view.rows[newest['id']][0].master)
     app.open_system_settings_window()
     self.assertEqual(app.main_notebook.select(),str(app.main_tabs['settings']))
     app.show_about()
     self.assertEqual(app.main_notebook.select(),str(app.main_tabs['about']))
     app.transcript_window.close()
     self.assertEqual(root.state(),'withdrawn')
     app.shutting_down=True
   finally:
    for callback in root.tk.splitlist(root.tk.call('after','info')): root.tk.call('after','cancel',callback)
    root.destroy()

if __name__=='__main__': unittest.main()
