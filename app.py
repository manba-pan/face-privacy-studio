"""Chinese standalone desktop UI. No server, account or editing application."""
from __future__ import annotations
import ctypes
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

if sys.stdout is None:
    sys.stdout=open(os.devnull,'w')
if sys.stderr is None:
    sys.stderr=open(os.devnull,'w')

from PIL import Image, ImageTk
import core

REGIONS={'整张脸':'full','上半脸':'upper','下半脸':'lower','仅眼睛':'eyes'}
STYLES={'马赛克':'mosaic','模糊':'blur','黑色遮挡':'solid'}
BG='#f3f5f8'
INK='#17253b'
MUTED='#66758a'
BLUE='#2563eb'


class App:
    def __init__(self,root):
        self.root=root
        root.title('视频一键打码工具 · 增强识别版 0.2.1')
        self.ui_scale=max(1.,float(root.tk.call('tk','scaling'))/(96/72))
        sw,sh=root.winfo_screenwidth(),root.winfo_screenheight()
        width=min(sw-80,round(1240*self.ui_scale))
        height=min(sh-100,round(850*self.ui_scale))
        root.geometry(f'{width}x{height}+{max(0,(sw-width)//2)}+{max(0,(sh-height)//2-40)}')
        root.minsize(min(sw-80,round(1080*self.ui_scale)),min(sh-100,round(760*self.ui_scale)))
        root.configure(bg=BG)
        self.events=queue.Queue()
        self.busy=False
        self.cancel=threading.Event()
        self.info=None
        self.analysis=None
        self.original=None
        self.index=0
        self.manual=[]
        self.output=None
        self.preview_job=None
        self.draw_mode=False
        self.drag_start=None
        self.drag_id=None
        self.photo=None
        self.image_box=(0,0,1,1)
        self.closing=False
        self.playing=False
        self.play_job=None
        self.region=tk.StringVar(value='整张脸')
        self.style=tk.StringVar(value='马赛克')
        self.strength=tk.IntVar(value=4)
        self.coverage=tk.DoubleVar(value=1.15)
        self.eye_height=tk.DoubleVar(value=1.0)
        self.missing=tk.StringVar(value='保持原画（需检查）')
        self.compare=tk.BooleanVar(value=False)
        self.position=tk.DoubleVar(value=0)
        self.start=tk.StringVar(value='00:00:00.00')
        self.end=tk.StringVar(value='00:00:05.00')
        self.status=tk.StringVar(value='选择一个视频开始。处理过程不上传视频。')
        self.frame_status=tk.StringVar(value='尚未导入视频')
        self.build()
        root.after(80,self.poll)
        root.protocol('WM_DELETE_WINDOW',self.on_close)

    def build(self):
        style=ttk.Style()
        style.theme_use('clam')
        style.configure('.',font=('Microsoft YaHei UI',10),background=BG,foreground=INK)
        style.configure('TButton',padding=(12,8),background='#e5ebf4',borderwidth=0)
        style.map('TButton',background=[('active','#d6e2f7')])
        style.configure('Primary.TButton',background=BLUE,foreground='white',padding=(16,10))
        style.map('Primary.TButton',background=[('active','#1d4ed8'),('disabled','#93addf')])
        style.configure('TCombobox',padding=5,fieldbackground='white')
        style.configure('TProgressbar',background=BLUE,troughcolor='#e2e8f0',borderwidth=0)
        header=tk.Frame(self.root,bg=INK,padx=24,pady=18)
        header.pack(fill='x')
        tk.Label(header,text='视频一键打码工具',font=('Microsoft YaHei UI',21,'bold'),fg='white',bg=INK).pack(side='left')
        tk.Label(header,text='增强识别 0.2.1  /  本地处理  /  独立运行',font=('Microsoft YaHei UI',10),fg='#a8bbd8',bg=INK).pack(side='left',padx=22)
        self.open_btn=ttk.Button(header,text='＋ 选择视频',style='Primary.TButton',command=self.open_video)
        self.open_btn.pack(side='right')
        # Reserve footer space before the expanding content on all DPI settings.
        footer=tk.Frame(self.root,bg='white',padx=20,pady=10)
        footer.pack(side='bottom',fill='x')
        content=tk.Frame(self.root,bg=BG,padx=20,pady=16)
        content.pack(fill='both',expand=True)
        left=tk.Frame(content,bg=BG)
        left.pack(side='left',fill='both',expand=True,padx=(0,18))
        self.name=tk.Label(left,text='01  导入视频 → 02  自动分析 → 03  预览并导出',anchor='w',font=('Microsoft YaHei UI',11,'bold'),bg=BG,fg=INK)
        self.name.pack(fill='x',pady=(0,9))
        self.canvas=tk.Canvas(left,bg='#101827',highlightthickness=0,cursor='arrow')
        self.canvas.pack(fill='both',expand=True)
        self.canvas.bind('<Configure>',lambda e:self.paint())
        self.canvas.bind('<ButtonPress-1>',self.drag_begin)
        self.canvas.bind('<B1-Motion>',self.drag_move)
        self.canvas.bind('<ButtonRelease-1>',self.drag_end)
        bar=tk.Frame(left,bg=BG)
        bar.pack(fill='x',pady=(8,0))
        self.play_btn=ttk.Button(bar,text='▶ 预览',command=self.toggle_play,state='disabled')
        self.play_btn.pack(side='left')
        ttk.Button(bar,text='‹ 上一帧',command=lambda:self.step(-1)).pack(side='left',padx=4)
        ttk.Button(bar,text='下一帧 ›',command=lambda:self.step(1)).pack(side='left')
        ttk.Checkbutton(bar,text='对比原画',variable=self.compare,command=self.paint).pack(side='right')
        self.slider=ttk.Scale(left,from_=0,to=1,variable=self.position,command=self.seek_changed)
        self.slider.pack(fill='x',pady=6)
        tk.Label(left,textvariable=self.frame_status,bg=BG,fg=MUTED,anchor='w').pack(fill='x')
        review_head=tk.Frame(left,bg=BG)
        review_head.pack(fill='x',pady=(15,5))
        tk.Label(review_head,text='需要回看的片段',font=('Microsoft YaHei UI',11,'bold'),bg=BG,fg=INK).pack(side='left')
        tk.Label(review_head,text='双击跳转；此列表不能发现所有漏检',bg=BG,fg=MUTED,font=('Microsoft YaHei UI',9)).pack(side='right')
        self.review=ttk.Treeview(left,columns=('time','reason'),show='headings',height=4)
        self.review.heading('time',text='时间范围')
        self.review.heading('reason',text='检测情况')
        self.review.column('time',width=round(285*self.ui_scale),stretch=False)
        self.review.column('reason',width=200)
        self.review.pack(fill='x')
        self.review.bind('<Double-1>',self.jump_review)
        side=tk.Frame(content,bg=BG,width=round(310*self.ui_scale))
        self.side=side
        side.pack(side='right',fill='y')
        side.pack_propagate(False)
        export_area=tk.Frame(side,bg=BG)
        export_area.pack(side='bottom',fill='x',pady=(10,0))
        self.tabs=ttk.Notebook(side)
        self.tabs.pack(fill='both',expand=True)
        auto_page=tk.Frame(self.tabs,bg=BG,padx=8,pady=10)
        manual_page=tk.Frame(self.tabs,bg=BG,padx=8,pady=10)
        self.tabs.add(auto_page,text='自动打码')
        self.tabs.add(manual_page,text='手动补码')
        side=auto_page
        self.section(side,'遮挡设置')
        self.combo(side,'遮挡部位',self.region,list(REGIONS))
        self.combo(side,'显示样式',self.style,list(STYLES))
        tk.Label(side,text='强度挡位',bg=BG,fg=MUTED,anchor='w').pack(fill='x',pady=(8,4))
        levels=tk.Frame(side,bg=BG)
        levels.pack(fill='x')
        for n in range(1,6):
            ttk.Radiobutton(levels,text=str(n),value=n,variable=self.strength,command=self.paint).pack(side='left',expand=True)
        self.range_control(side,'覆盖范围',self.coverage,1.,1.65)
        self.range_control(side,'眼睛横条高度',self.eye_height,.65,2.)
        self.combo(side,'未检测到任何人脸时',self.missing,['整帧黑色遮挡','保持原画（需检查）'])
        tk.Label(side,text='检测到的人脸统一处理，最多 6 张。\n黑色遮挡不受强度挡位影响。',bg=BG,fg=MUTED,justify='left',font=('Microsoft YaHei UI',9)).pack(anchor='w',pady=(6,10))
        side=manual_page
        self.section(side,'手动补码')
        tk.Label(side,text='设置时间范围，再在画面拖出矩形。\n补码框位置固定；移动目标可分段补码。',bg=BG,fg=MUTED,justify='left',font=('Microsoft YaHei UI',9)).pack(anchor='w',pady=(0,6))
        times=tk.Frame(side,bg=BG)
        times.pack(fill='x')
        ttk.Entry(times,textvariable=self.start,width=13).pack(side='left')
        tk.Label(times,text=' 至 ',bg=BG).pack(side='left')
        ttk.Entry(times,textvariable=self.end,width=13).pack(side='left')
        mb=tk.Frame(side,bg=BG)
        mb.pack(fill='x',pady=6)
        ttk.Button(mb,text='当前时间作起点',command=self.set_start).pack(side='left')
        self.draw_btn=ttk.Button(mb,text='画补码框',command=self.arm_draw)
        self.draw_btn.pack(side='right')
        self.manual_list=tk.Listbox(side,height=3,font=('Microsoft YaHei UI',9),bg='white',fg=INK,relief='flat',highlightthickness=1,highlightbackground='#dce3ed',exportselection=False)
        self.manual_list.pack(fill='x')
        ttk.Button(side,text='删除选中补码框',command=self.remove_manual).pack(fill='x',pady=(4,10))
        self.export_btn=ttk.Button(export_area,text='导出打码视频',style='Primary.TButton',command=self.export,state='disabled')
        self.export_btn.pack(fill='x',pady=(6,4))
        self.folder_btn=ttk.Button(export_area,text='打开成片所在文件夹',command=self.open_output,state='disabled')
        self.folder_btn.pack(fill='x')
        self.progress=ttk.Progressbar(footer,maximum=100)
        self.progress.pack(fill='x',pady=(0,7))
        tk.Label(footer,textvariable=self.status,bg='white',fg=INK,anchor='w',wraplength=1020,justify='left').pack(side='left',fill='x',expand=True)
        self.cancel_btn=ttk.Button(footer,text='取消处理',command=self.cancel_job,state='disabled')
        self.cancel_btn.pack(side='right')

    def section(self,parent,text):
        tk.Label(parent,text=text,bg=BG,fg=INK,font=('Microsoft YaHei UI',12,'bold'),anchor='w').pack(fill='x',pady=(0,6))

    def combo(self,parent,label,var,values):
        tk.Label(parent,text=label,bg=BG,fg=MUTED,anchor='w').pack(fill='x',pady=(6,3))
        combo=ttk.Combobox(parent,textvariable=var,values=values,state='readonly')
        combo.pack(fill='x')
        combo.bind('<<ComboboxSelected>>',lambda e:self.paint())

    def range_control(self,parent,label,var,low,high):
        row=tk.Frame(parent,bg=BG)
        row.pack(fill='x',pady=(7,0))
        tk.Label(row,text=label,bg=BG,fg=MUTED).pack(side='left')
        val=tk.Label(row,text=f'{var.get():.2f}×',bg=BG,fg=INK)
        val.pack(side='right')
        def changed(value):
            val.config(text=f'{float(value):.2f}×')
            self.paint()
        ttk.Scale(parent,from_=low,to=high,variable=var,command=changed).pack(fill='x')

    def settings(self):
        return core.Settings(REGIONS[self.region.get()],STYLES[self.style.get()],self.strength.get(),
                             self.coverage.get(),self.eye_height.get(),
                             'full_frame' if self.missing.get()=='整帧黑色遮挡' else 'keep')

    def launch_job(self,kind,fn):
        self.stop_play()
        self.busy=True
        self.cancel.clear()
        self.progress['value']=0
        self.open_btn.config(state='disabled')
        self.export_btn.config(state='disabled')
        self.play_btn.config(state='disabled')
        self.cancel_btn.config(state='normal')
        self.job_kind=kind
        self.job_started=time.monotonic()
        self.frozen_controls=[]
        if kind=='导出':
            def freeze(parent):
                for widget in parent.winfo_children():
                    if 'state' in widget.keys():
                        self.frozen_controls.append((widget,str(widget.cget('state'))))
                        widget.configure(state='disabled')
                    freeze(widget)
            freeze(self.side)
        def worker():
            try:
                value=fn()
                self.events.put(('done',kind,value))
            except core.Cancelled:
                self.events.put(('cancelled',kind,None))
            except Exception as exc:
                self.events.put(('error',kind,(str(exc),traceback.format_exc())))
        self.worker=threading.Thread(target=worker,daemon=True)
        self.worker.start()

    def job_progress(self,fraction,index,faces):
        self.events.put(('progress',fraction,(index,faces)))

    def open_video(self,path=None):
        if self.busy:
            return
        path=path or filedialog.askopenfilename(title='选择需要打码的视频',filetypes=[('视频','*.mp4 *.mov *.mkv *.avi *.m4v *.webm'),('所有文件','*.*')])
        if not path:
            return
        try:
            info=core.probe(path)
            first=core.read_frame(info,0)
        except Exception as exc:
            messagebox.showerror('无法导入',str(exc))
            return
        if self.analysis:
            self.analysis.close()
        self.analysis=None
        self.info=info
        self.original=first
        self.index=0
        self.manual=[]
        self.output=None
        self.manual_list.delete(0,'end')
        self.review.delete(*self.review.get_children())
        self.position.set(0)
        self.slider.config(to=max(0,info.frames-1))
        self.name.config(text=Path(path).name)
        self.frame_status.set(f'{info.width} × {info.height}  ·  {info.fps:.2f} fps  ·  {core.timecode(info.duration)}')
        self.start.set(core.timecode(0))
        self.end.set(core.timecode(min(5,info.duration)))
        self.folder_btn.config(state='disabled')
        self.status.set('正在分析人脸。分析结束后可以切换部位、强度并预览。')
        self.paint()
        self.launch_job('分析',lambda:core.analyze(info,self.cancel,self.job_progress))

    def poll(self):
        try:
            while True:
                kind,a,b=self.events.get_nowait()
                if kind=='progress':
                    self.progress['value']=a*100
                    elapsed=time.monotonic()-self.job_started
                    eta=elapsed*(1-a)/a if a>.001 else 0
                    ending=f'预计还需 {core.timecode(eta)}' if a<.98 else '正在完成最后处理…'
                    self.status.set(f'正在{self.job_kind}  {a*100:.0f}%  ·  {ending}')
                else:
                    self.busy=False
                    for widget,state in self.frozen_controls:
                        widget.configure(state=state)
                    self.frozen_controls=[]
                    self.open_btn.config(state='normal')
                    self.cancel_btn.config(state='disabled')
                    if kind=='done':
                        self.progress['value']=100
                        if a=='分析':
                            self.analysis=b
                            self.slider.config(to=max(0,b.info.frames-1))
                            for j,item in enumerate(b.review):
                                lo=core.timecode(item['start']/self.info.fps)
                                hi=core.timecode((item['end']+1)/self.info.fps)
                                self.review.insert('','end',iid=str(j),values=(f'{lo} — {hi}',item['reason']))
                            detected=sum(bool(faces) for faces in b.faces)
                            if detected==0:
                                self.status.set('整段未检测到人脸。请使用手动补码；调高强度不会改善识别。')
                                if not self.closing:
                                    messagebox.showwarning('整段未识别到人脸','自动打码没有找到人脸。\n\n请在“手动补码”中补充遮挡，或使用其他素材重新分析。\n“保持原画”会让这些画面没有自动遮挡。')
                            else:
                                self.status.set(f'分析完成：{detected}/{len(b.faces)} 帧有人脸。{len(b.review)} 段需回看；预览无声，导出保留音轨。')
                            # Open a representative detected frame instead of a
                            # black fallback on an introductory back-of-head shot.
                            best_start=best_length=run_start=run_length=0
                            for frame_index,faces in enumerate(b.faces):
                                if faces:
                                    if run_length==0:
                                        run_start=frame_index
                                    run_length+=1
                                    if run_length>best_length:
                                        best_start,best_length=run_start,run_length
                                else:
                                    run_length=0
                            self.show_frame(best_start+best_length//2 if best_length else 0)
                        else:
                            self.output=b
                            self.folder_btn.config(state='normal')
                            self.status.set('已导出：'+b)
                            if not self.closing:
                                messagebox.showinfo('导出完成','打码视频已保存，原视频保留。\n\n'+b+'\n\n请回看成片确认遮挡效果。')
                    elif kind=='cancelled':
                        self.status.set('已取消。没有保存未完成的成片。')
                    elif kind=='error':
                        self.status.set(a+'未完成：'+b[0])
                        self.log_error(b[1])
                        if not self.closing:
                            messagebox.showerror(a+'未完成',b[0])
                    self.export_btn.config(state='normal' if self.analysis else 'disabled')
                    self.play_btn.config(state='normal' if self.analysis else 'disabled')
                    if self.closing:
                        self.cleanup()
                        return
        except queue.Empty:
            pass
        self.root.after(80,self.poll)

    def log_error(self,text):
        try:
            directory=Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'FacePrivacy'
            directory.mkdir(parents=True,exist_ok=True)
            (directory/'last-error.txt').write_text(text,encoding='utf8')
        except OSError:
            pass

    def seek_changed(self,value):
        if not self.analysis or self.busy:
            return
        self.stop_play()
        if self.preview_job:
            self.root.after_cancel(self.preview_job)
        self.preview_job=self.root.after(80,lambda:self.show_frame(round(float(value))))

    def show_frame(self,index):
        self.preview_job=None
        if not self.analysis:
            return
        self.index=max(0,min(self.info.frames-1,int(index)))
        try:
            self.original=core.read_preview(self.analysis,self.index)
        except Exception as exc:
            self.status.set(str(exc))
            return
        self.position.set(self.index)
        faces=len(self.analysis.faces[self.index])
        self.frame_status.set(f'{core.timecode(self.index/self.info.fps)} / {core.timecode(self.info.duration)}  ·  检测到 {faces} 张脸  ·  无声预览')
        self.paint()

    def paint(self):
        if not hasattr(self,'canvas'):
            return
        cw,ch=max(1,self.canvas.winfo_width()),max(1,self.canvas.winfo_height())
        if self.original is None:
            self.canvas.delete('all')
            self.canvas.create_text(cw/2,ch/2-22,text='先选择一段采访视频',fill='#e6edf8',font=('Microsoft YaHei UI',21,'bold'))
            self.canvas.create_text(cw/2,ch/2+23,text='整脸 · 半脸 · 眼睛    /    五挡强度',fill='#8fa5c5',font=('Microsoft YaHei UI',12))
            return
        rgb=self.original
        if self.analysis and not self.compare.get():
            rgb=core.render_frame(rgb,self.analysis.faces[self.index],self.settings(),self.manual,self.index)
        h,w=rgb.shape[:2]
        factor=min(cw/w,ch/h)
        dw,dh=max(1,round(w*factor)),max(1,round(h*factor))
        ox,oy=(cw-dw)//2,(ch-dh)//2
        self.image_box=(ox,oy,dw,dh)
        self.photo=ImageTk.PhotoImage(Image.fromarray(rgb).resize((dw,dh),Image.Resampling.LANCZOS))
        self.canvas.delete('all')
        self.canvas.create_image(ox,oy,image=self.photo,anchor='nw')
        label='原画对比' if self.compare.get() else ('打码预览' if self.analysis else '原画 · 分析后显示打码')
        self.canvas.create_rectangle(12,12,210,42,fill=INK,outline='')
        self.canvas.create_text(23,27,text=label,fill='white',anchor='w',font=('Microsoft YaHei UI',10))
        if self.draw_mode:
            self.canvas.create_text(cw/2,ch-22,text='拖动绘制补码矩形',fill='#67e8f9',font=('Microsoft YaHei UI',12,'bold'))

    def step(self,offset):
        if self.analysis and not self.busy:
            self.stop_play()
            self.show_frame(self.index+offset)

    def stop_play(self):
        self.playing=False
        if self.play_job:
            self.root.after_cancel(self.play_job)
            self.play_job=None
        if hasattr(self,'play_btn'):
            self.play_btn.config(text='▶ 预览')

    def toggle_play(self):
        if self.playing:
            self.stop_play()
            return
        if not self.analysis or self.busy:
            return
        self.playing=True
        self.play_btn.config(text='Ⅱ 暂停')
        self.play_start=time.monotonic()
        self.play_index=0 if self.index>=self.info.frames-1 else self.index
        self.play_tick()

    def play_tick(self):
        if not self.playing:
            return
        index=self.play_index+int((time.monotonic()-self.play_start)*self.info.fps)
        self.show_frame(index)
        if index>=self.info.frames-1:
            self.stop_play()
        else:
            self.play_job=self.root.after(65,self.play_tick)

    def jump_review(self,event):
        sel=self.review.selection()
        if sel and self.analysis and not self.busy:
            item=self.analysis.review[int(sel[0])]
            self.start.set(core.timecode(item['start']/self.info.fps))
            self.end.set(core.timecode((item['end']+1)/self.info.fps))
            self.stop_play()
            self.show_frame(item['start'])

    def set_start(self):
        if self.info:
            self.start.set(core.timecode(self.index/self.info.fps))

    def arm_draw(self):
        if not self.analysis or self.busy:
            return
        self.stop_play()
        self.draw_mode=not self.draw_mode
        self.canvas.config(cursor='crosshair' if self.draw_mode else 'arrow')
        self.draw_btn.config(text='取消画框' if self.draw_mode else '画补码框')
        self.paint()

    def normalized_point(self,event):
        ox,oy,w,h=self.image_box
        return (max(0.,min(1.,(event.x-ox)/w)),max(0.,min(1.,(event.y-oy)/h)))

    def drag_begin(self,event):
        if self.draw_mode:
            self.drag_start=self.normalized_point(event)
            self.drag_id=self.canvas.create_rectangle(event.x,event.y,event.x,event.y,outline='#22d3ee',width=3)

    def drag_move(self,event):
        if self.draw_mode and self.drag_start and self.drag_id:
            ox,oy,w,h=self.image_box
            x,y=self.normalized_point(event)
            self.canvas.coords(self.drag_id,ox+self.drag_start[0]*w,oy+self.drag_start[1]*h,ox+x*w,oy+y*h)

    def drag_end(self,event):
        if not self.draw_mode or not self.drag_start:
            return
        x,y=self.normalized_point(event)
        sx,sy=self.drag_start
        self.drag_start=None
        self.drag_id=None
        rect=[min(x,sx),min(y,sy),max(x,sx),max(y,sy)]
        try:
            if rect[2]-rect[0]<.008 or rect[3]-rect[1]<.008:
                raise ValueError('补码框太小，请重新拖动画框。')
            start=core.parse_time(self.start.get())
            end=core.parse_time(self.end.get())
            if not 0<=start<end<=self.info.duration+.02:
                raise ValueError('结束时间应晚于开始时间，且不超过视频时长。')
            box={'start':max(0,int(start*self.info.fps)),
                 'end':min(self.info.frames-1,max(0,int(end*self.info.fps+.001)-1)), 'rect':rect}
            self.manual.append(box)
            self.manual_list.insert('end',f'{len(self.manual)}. {core.timecode(start)} → {core.timecode(end)}')
            self.status.set('已添加固定补码框。可逐帧回看，确认覆盖所需片段。')
        except Exception as exc:
            messagebox.showerror('补码框未添加',str(exc))
        self.arm_draw()

    def remove_manual(self):
        if self.busy:
            return
        sel=self.manual_list.curselection()
        if sel:
            del self.manual[sel[0]]
            self.manual_list.delete(sel[0])
            self.paint()

    def export(self,destination=None):
        if self.busy or not self.analysis:
            return
        src=Path(self.info.path)
        destination=destination or filedialog.asksaveasfilename(title='另存打码视频',initialdir=src.parent,
            initialfile=src.stem+'_已打码.mp4',defaultextension='.mp4',filetypes=[('MP4 视频','*.mp4')])
        if not destination:
            return
        if Path(destination).exists():
            messagebox.showerror('请换一个文件名','为保留已有文件，请使用一个尚不存在的新文件名。')
            return
        settings=self.settings()
        manual=json.loads(json.dumps(self.manual))
        analysis=self.analysis
        self.status.set('开始导出。将保留第一条音轨，另存为 MP4。')
        self.launch_job('导出',lambda:core.export_video(analysis,destination,settings,manual,self.cancel,self.job_progress))

    def open_output(self):
        if self.output:
            os.startfile(str(Path(self.output).parent))

    def cancel_job(self):
        self.cancel.set()
        self.cancel_btn.config(state='disabled')
        self.status.set('正在取消并清理临时文件…')

    def on_close(self):
        if self.busy:
            self.closing=True
            self.cancel_job()
        else:
            self.cleanup()

    def cleanup(self):
        self.stop_play()
        if self.analysis:
            self.analysis.close()
        self.root.destroy()


def main():
    if len(sys.argv)==4 and sys.argv[1]=='--verify-runtime':
        # Exercise the actual frozen application and bundled components.
        report=Path(sys.argv[3]+'.verification.json')
        analysis=None
        try:
            info=core.probe(sys.argv[2])
            analysis=core.analyze(info)
            output=core.export_video(analysis,sys.argv[3],core.Settings(region='eyes'))
            report.write_text(json.dumps({'ok':True,'frames':info.frames,'duration':info.duration,
                'detected_frames':sum(bool(x) for x in analysis.faces),'output':output,
                'output_container_duration':core.media_duration(output)},ensure_ascii=False,indent=2),encoding='utf8')
        except Exception:
            report.write_text(json.dumps({'ok':False,'error':traceback.format_exc()},ensure_ascii=False,indent=2),encoding='utf8')
            raise
        finally:
            if analysis:
                analysis.close()
        return
    if os.name=='nt':
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root=tk.Tk()
    app=App(root)
    if len(sys.argv)>1 and Path(sys.argv[1]).is_file():
        root.after(200,lambda:app.open_video(sys.argv[1]))
    root.mainloop()


if __name__=='__main__':
    main()
