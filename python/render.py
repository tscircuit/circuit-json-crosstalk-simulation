"""Actual ParaView screenshots of saved Gmsh tetrahedra and Palace fields."""
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
import paraview
from vtkmodules.vtkCommonCore import vtkSMPTools, vtkMultiThreader
from paraview.simple import (PVDReader, XMLUnstructuredGridReader, Calculator,
    CreateView, Show, Hide, ColorBy, GetColorTransferFunction, GetOpacityTransferFunction,
    GetScalarBar, Slice, SaveScreenshot, SaveState, Delete, Render)


def render(root):
    vtkSMPTools.Initialize(1)
    vtkMultiThreader.SetGlobalMaximumNumberOfThreads(1)
    config=json.loads((root/'eye-input.json').read_text())
    camera={'CameraPosition':[9,-11,8],'CameraFocalPoint':[0,0,.1],
            'CameraViewUp':[0,0,1],'CameraParallelProjection':1,'CameraParallelScale':2.7}
    limits=[100.,100000.]
    outputs=[]
    for name,case in config['cases'].items():
        directory=Path(case['channel'])
        receipt=json.loads((directory/'native-receipt.json').read_text())
        if receipt['native_status']!='passed': raise ValueError('Only completed native fields may be rendered')
        copper=XMLUnstructuredGridReader(FileName=[str(directory/'copper-surface.vtu')])
        dielectric=XMLUnstructuredGridReader(FileName=[str(directory/'dielectric-mesh.vtu')])
        view=CreateView('RenderView');view.ViewSize=[640,420];view.UseColorPaletteForBackground=0;view.Background=[1,1,1]
        view.OrientationAxesVisibility=0;view.CenterAxesVisibility=0
        for k,v in camera.items():setattr(view,k,v)
        d=Show(dielectric,view);d.Representation='Surface With Edges';d.DiffuseColor=[.85,.89,.86];d.EdgeColor=[.3,.36,.34];d.Opacity=.5
        c=Show(copper,view);c.Representation='Surface With Edges';c.DiffuseColor=[.75,.34,.12];c.EdgeColor=[.25,.15,.08]
        d.ColorArrayName=['POINTS',''];c.ColorArrayName=['POINTS','']
        Render(view)
        for k,v in camera.items():setattr(view,k,v)
        SaveScreenshot(str(root/f'{name}-mesh.png'),view,ImageResolution=[640,420])
        Hide(dielectric,view)
        field_path=directory/'postpro/paraview/driven/excitation_2/excitation_2.pvd'
        datasets=ET.parse(field_path).findall('.//DataSet')
        if len(datasets)!=1 or float(datasets[0].attrib['timestep'])!=1:
            raise ValueError('Expected exactly the saved1GHz excitation2 field')
        field=PVDReader(FileName=str(field_path));field.UpdatePipeline(time=1)
        cut=Slice(Input=field);cut.SliceType='Plane';cut.SliceType.Origin=[0,0,.1];cut.SliceType.Normal=[1,0,0]
        magnitude=Calculator(Input=cut);magnitude.ResultArrayName='E_magnitude'
        magnitude.Function='sqrt(dot(E_real,E_real)+dot(E_imag,E_imag))'
        display=Show(magnitude,view);display.Representation='Surface'
        ColorBy(display,('POINTS','E_magnitude'))
        lut=GetColorTransferFunction('E_magnitude')
        # Fixed Viridis control colors avoid version-dependent preset names.
        lut.RGBPoints=[100,.267,.005,.329,1000,.23,.322,.546,10000,.128,.566,.551,100000,.993,.906,.143]
        lut.ColorSpace='RGB'
        lut.RescaleTransferFunction(*limits);lut.UseLogScale=1
        GetOpacityTransferFunction('E_magnitude').RescaleTransferFunction(*limits)
        display.SetScalarBarVisibility(view,True)
        bar=GetScalarBar(lut,view);bar.Title='|E| (V/m)';bar.ComponentTitle='';bar.TitleColor=[.2,.2,.2];bar.LabelColor=[.2,.2,.2];bar.TitleFontSize=12;bar.LabelFontSize=10
        Render(view);SaveScreenshot(str(root/f'{name}-field.png'),view,ImageResolution=[640,420])
        SaveState(str(root/f'{name}-field.pvsm'))
        outputs.append({'case':name,'field':str(field_path),'frequency_hz':1e9,'excitation':2,
                        'normalization':'unit incident power','quantity':'norm(E_real+iE_imag)',
                        'color_limits_v_per_m':limits,'scale':'logarithmic; clipped to shared limits',
                        'camera':camera,'slice_plane':'X=0mm displayed within true3D copper geometry; no z exaggeration'})
        for proxy in [magnitude,cut,field,dielectric,copper,view]:Delete(proxy)
    (root/'render-settings.json').write_text(json.dumps({'paraview_version':paraview.__version__,'vtk_smp_threads':vtkSMPTools.GetEstimatedNumberOfThreads(),'outputs':outputs},indent=2)+'\n')


if __name__=='__main__':render(Path(sys.argv[1]).resolve())
