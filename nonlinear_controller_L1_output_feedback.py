#!/usr/bin/env python

# PURPOSE
# This controller takes in current state information through the /current_coordinates topic and requests waypoint information from the /path_follower node for the desired state. Based on the difference between these two states, the controller computes outputs and sends commands to the drone.
# This controller also has a keypress handler to allow for manual control of the vehicle.

# SUBSCRIBED TOPICS
# /current_coordinates
# /path_coordinates
# /ardrone/navdata

# PUBLISHED TOPICS
# /cmd_vel_ideal [rad]
# /ardrone/land 
# /ardrone/takeoff
# /ardrone/reset
# /waypoint_request


####################
# IMPORT LIBRARIES #
####################

# Import ROS libraries, rospy, and load manifest file for access to project dependencies
import rospy
import roslib; roslib.load_manifest('dsl__controllers__basic')

import time
import sys
import math
import numpy as np

import csv

# Load the DroneVideoDisplay class, which handles video display
from drone_video_display import DroneVideoDisplay
from drone_status import DroneStatus

# The GUI libraries
from PySide import QtCore, QtGui


###################
# IMPORT MESSAGES #
###################

from dsl__utilities__msg.msg import StateVector
from dsl__utilities__msg.msg import StateData
from ardrone_autonomy.msg import Navdata
from geometry_msgs.msg import Twist
from std_msgs.msg import Empty  
from std_msgs.msg import Bool
from std_msgs.msg import Float32

from rospy.numpy_msg import numpy_msg


# For shutting down the QT application window in CTRL-C
import signal
from PyQt4.QtCore import QTimer
from PyQt4.QtGui import QApplication, QMessageBox
def sigint_handler(*args):
    """Handler for the SIGINT signal."""
    QApplication.quit()


##################       Key Mapping Object     ################################
class KeyMapping(object):
  ForwardLeft      = QtCore.Qt.Key.Key_W
  Forward          = QtCore.Qt.Key.Key_E
  ForwardRight     = QtCore.Qt.Key.Key_R
  Right            = QtCore.Qt.Key.Key_F
  BackwardRight    = QtCore.Qt.Key.Key_V
  Backward         = QtCore.Qt.Key.Key_C
  BackwardLeft     = QtCore.Qt.Key.Key_X
  Left             = QtCore.Qt.Key.Key_S
  YawLeft          = QtCore.Qt.Key.Key_A
  YawRight         = QtCore.Qt.Key.Key_G
  IncreaseAltitude = QtCore.Qt.Key.Key_Q
  DecreaseAltitude = QtCore.Qt.Key.Key_Z
  Takeoff          = QtCore.Qt.Key.Key_Y
  Land             = QtCore.Qt.Key.Key_H
  Emergency        = QtCore.Qt.Key.Key_Space
  StartHover       = QtCore.Qt.Key.Key_I
  EndHover         = QtCore.Qt.Key.Key_K
  GoInitPos        = QtCore.Qt.Key.Key_O # Custom functions
  StartExp         = QtCore.Qt.Key.Key_P # 
  LandAll          = QtCore.Qt.Key.Key_J
  TakeoffAll       = QtCore.Qt.Key.Key_U
  SaveToFile	   = QtCore.Qt.Key.Key_T #My functions to dump into csv file
  ReadFromFile     = QtCore.Qt.Key.Key_B #Also mine
  
  ChangePitchOut     = QtCore.Qt.Key.Key_M


#####################   Useful Structures for the Controller ###################

class State:
  x      = np.array([0,0,0])
  x_dot  = np.array([0,0,0])
  x_ddot = np.array([0,0,0])

  rpy    = np.array([0,0,0],dtype=np.float64)

  def __init__(self, x_     =np.array([0,0,0]),
                     x_dot_ =np.array([0,0,0]),
                     x_ddot_=np.array([0,0,0]),
                     rpy_   =np.array([0,0,0])):
    self.x      = x_
    self.x_dot  = x_dot_
    self.x_ddot = x_ddot_
    self.rpy    = rpy_

  @classmethod
  def fromState(cls, ss):
    new_class = cls(ss.x, ss.x_dot, ss.x_ddot, ss.rpy)
    return new_class

class DroneCommand:
  roll    = 0
  pitch   = 0
  yaw_dot = 0
  z_dot   = 0

  twist = Twist()

class Status:
  drone_state       = -1 # hover/flight etc
  keyboard_override = 0
  hover             = 0
  request_waypoint  = Bool(True)
  t_last_cmd        = 0


#####################   Main Controller Code    ################################

# class DroneController(DroneVideoDisplay):
# 
#  
#   def __init__(self):
#     super(DroneController,self).__init__)

class DroneController(DroneVideoDisplay):

  # Member Variables
  current_state = State()
  desired_state = State()
  command       = DroneCommand()
  status        = Status()

  navdata_rpy = np.array([[0.0],[0.0],[0.0]])

  # some parameters
  tau_x = 0.6 #original was 1.5
  tau_y = 0.6 #original was 1.5
  tau_z = 0.8
  tau_w = 0.7
  zeta  = 0.707 #original was 0.707

  g = 9.81
  max_euler = 0.
  max_vz    = 0.
  max_yaw   = 0.

  #*****************************************************************************

  # Constructor
  def __init__(self):
    super(DroneController,self).__init__()
    rospy.loginfo('Initializing Non-linear Controller---------------------------')

    # Subscribers 
    # path_coordinates = desired coordinates
    # current_coordinates = vicon coordinates
    # Publishers
    self.pubLand    = rospy.Publisher('ardrone/land', Empty, queue_size = 2)
    self.pubTakeoff = rospy.Publisher('ardrone/takeoff', Empty, queue_size = 2)
    self.pubReset   = rospy.Publisher('ardrone/reset', Empty, queue_size = 2)
    self.pubCommand = rospy.Publisher('cmd_vel_ideal', Twist, queue_size = 2)
    self.pubGoInitPos = rospy.Publisher('/go_init_pos', Empty, queue_size = 2)
    self.pubStartExp = rospy.Publisher('/start_exp', Empty, queue_size = 2)
    self.pubLandAll = rospy.Publisher('/land_all', Empty, queue_size = 2)
    self.pubTakeoffAll = rospy.Publisher('/takeoff_all', Empty, queue_size = 2)
    self.pubSaveToFile = rospy.Publisher('/save_to_file', Empty, queue_size = 2)
    self.pubReadFromFile = rospy.Publisher('/read_from_file',Empty, queue_size = 2)
    
    # Publish L1 data
    self.pubParama = rospy.Publisher('ardrone/parama',Float32, queue_size = 2)
    self.pubParamb = rospy.Publisher('ardrone/paramb',Float32, queue_size = 2)
    self.pubParamc = rospy.Publisher('ardrone/paramc',Float32, queue_size = 2)
    self.pubL1des_x = rospy.Publisher('ardrone/l1des_x',Float32, queue_size = 2)
    self.pubL1des_y = rospy.Publisher('ardrone/l1des_y',Float32, queue_size = 2)
    self.pubL1des_z = rospy.Publisher('ardrone/l1des_z',Float32, queue_size = 2)
    self.pubXref_x = rospy.Publisher('ardrone/xref_x',Float32, queue_size = 2)
    self.pubXref_y = rospy.Publisher('ardrone/xref_y',Float32, queue_size = 2)
    self.pubXref_z = rospy.Publisher('ardrone/xref_z',Float32, queue_size = 2)
    self.pubXcurr_x = rospy.Publisher('ardrone/xcurr_x',Float32, queue_size = 2)
    self.pubXcurr_y = rospy.Publisher('ardrone/xcurr_y',Float32, queue_size = 2)
    self.pubXcurr_z = rospy.Publisher('ardrone/xcurr_z',Float32, queue_size = 2)
    self.pubXdotcurr_x = rospy.Publisher('ardrone/xdotcurr_x',Float32, queue_size = 2)
    self.pubXdotcurr_y = rospy.Publisher('ardrone/xdotcurr_y',Float32, queue_size = 2)
    self.pubXdotcurr_z = rospy.Publisher('ardrone/xdotcurr_z',Float32, queue_size = 2)

    self.sub_cur     = rospy.Subscriber('estimated_state', StateVector, self.updateCurrentState)
    self.sub_navdata = rospy.Subscriber('ardrone/navdata', Navdata, self.updateNavdata)
    self.sub_des     = rospy.Subscriber('path_coordinates', StateData, self.updateDesiredState)
    self.sub_land_all = rospy.Subscriber('/land_all', Empty, self.SendLand)    
    self.sub_takeoff_all = rospy.Subscriber('/takeoff_all', Empty, self.SendTakeoff)

    # Control Parameters
    self.max_euler = rospy.get_param('ardrone_driver/euler_angle_max', 0.26)  # rads
    self.max_vz    = rospy.get_param('ardrone_driver/control_vz_max', 1.0)  # m/sec
    self.max_yaw   = rospy.get_param('ardrone_driver/control_yaw', 1.75)  # rads/s

    cmd_rate       = rospy.get_param('cmd_rate', 70);         # command rate (Hz)
    self.COMMAND_PERIOD = 1.0/cmd_rate

    # Design Parameters
    print "Getting parameters" 
    self.tau_x = rospy.get_param("~tau_x", 0.7)
    self.tau_y = rospy.get_param("~tau_y", 0.7)
    self.tau_z = rospy.get_param("~tau_z", 0.7)
    self.tau_w = rospy.get_param("~tau_w", 1.5)
    self.zeta  = rospy.get_param("~zeta",0.707)
    self.L1_type = rospy.get_param("~L1_type",1)
    self.simulation_flag = rospy.get_param("~simulation_flag", 0)

    print self.L1_type


    self.symmetry_check = 0 # NOTE: TEST X-Y SYMMETRY if symmetry_check = 1
    self.angles_log = 0 # if 1, l1_angles.csv containing rpy angles is created and logged

    ###########################################################################
    # Add L1 Takeover Delay
    ###########################################################################
    self.start_flight_timer = False
    self.print_L1_status = False
    self.print_L1_status_flag = True
    #self.delay_until_L1_start = 0.0
    self.start_time = 0.0 #initialization, value is overwritten at takeoff
    self.delay_until_L1_start = 15.0 #sec

    ###########################################################################
    # Artificial Output Disturbance
    ###########################################################################
    self.change_pitch = False
    self.change_output_factor = 0.5

    '''
    ###########################################################################
    # L1 adaptive output control parameters -- for L1 control of x_dot
    ###########################################################################
    '''
    
    # directory in which to save CSV file
    self.save_dir = '/home/dsl5/L1_experiments/'

    # start time for logging
    self.current_time = time.strftime("%Y-%m-%d_%H-%M-%S_",time.localtime())

    ### Initialize zero vectors 

    self.x_L1_des = np.array([[0.0],[0.0],[0.0]])

    # 3rd order filter initialization
    self.u_dot = np.array([[0.0],[0.0],[0.0]])
    self.u = np.array([[0.0],[0.0],[0.0]])
    self.y_ddot = np.array([[0.0],[0.0],[0.0]])
    self.y_dot = np.array([[0.0],[0.0],[0.0]])
    self.y = np.array([[0.0],[0.0],[0.0]])

    self.oldtime = rospy.get_rostime() # for integration


    self.sigma_hat = np.array([[0.0],[0.0],[0.0]]) # adaptive estimate

    self.desired_vel = np.array([[0.0],[0.0],[0.0]])

    self.x_ref = np.array([[0.0],[0.0],[0.0]]) # initialize reference x position

    # errors (primarily for derivative controller)
    self.old_pos_error = np.array([[0.0],[0.0],[0.0]])
    self.old_err = np.array([[0.0],[0.0],[0.0]])
    self.old_rp_error = np.array([[0.0],[0.0]])


    ### Now initialize L1 parameters based on L1 architecture
    if self.L1_type == 1:
      print "L1 x-y-z position controller with piecewice constant adaptation"
      
      ### L1 low pass filter
      #self.K = 20*np.diag(np.array([6.0, 6.0, 8.0]))
      #self.K = np.diag(np.array([1.0, 1.0, 1.0]))
      self.K = np.diag(np.array([0.8, 0.8, 2.0]))
      self.omega_0 = np.eye(3)

      # L1 parameter adaptation
      self.Gamma = 100.0

      ### Reference Model -- first-order reference model M(s) = m/(s+m)*eye(3) ###
      # M_i(s) = m_i/(s+m_i), i = x,y,z

      ##self.A_m = np.diag(np.array([-4.0, -1.0, -1.0]))
      self.A_m = np.diag(np.array([-25.0, -25.0, -4.5])) # USE THIS FOR L1 OUTPUT POSITION
      self.B_m = -self.A_m
    
      #self.B_inv = np.linalg.inv(self.B_m)

      ### Create CSV file to keep a record of the parameters that produced the recorded results
      with open(self.save_dir + self.current_time + 'l1_experiment_info.csv','ab') as l1_info:
        writer = csv.writer(l1_info)
        writer.writerow(['L1_type', 'omega_cx', 'omega_cy', 'omega_cz', 'm_x', 'm_y', 'm_z', 'N/A', 'N/A', 'N/A', 'N/A', 'N/A', 'N/A', 'Gamma'])
        writer.writerow(np.array( [self.L1_type, self.K[0][0], self.K[1][1], self.K[2][2], self.A_m[0][0], self.A_m[1][1], self.A_m[2][2], self.omega_0[0][0], self.omega_0[1][1], self.omega_0[2][2], 0, 0, 0, self.Gamma ] ))

    
    elif self.L1_type == 2:

      # L1 parameter adaptation
      self.Gamma =1000.0

      ### Projection Operator - convex set ###
      self.sigma_hat_max = 200.0 # maximum absolute nominal value of sigma_hat
      self.epsilon_sigma = 0.1 # tolerance on maximum sigma_hat

      self.delay_until_L1_start = 10.0 #sec

      self.LPF_type = 1

      # Check if running in simulation or on real system
      if self.simulation_flag:
        print "\n !!!!!  RUNNING IN SIMULATION !!!!!\n"
        print "L1 x-y-z translational velocity controller with projection based adaptation"

        ### L1 low pass filter cutoff frequency   
        #self.omega_cutoff = np.diag( np.array( [1.4, 1.4, 1.5] ) ) # first order
        #self.omega_cutoff = np.diag( np.array( [1.85, 1.3, 6.5] ) ) # third order (om_cz=9.0 is too high)
        self.omega_cutoff = np.diag( np.array( [1.7, 1.7, 6.75] ) ) # third order


        ### Reference Model -- first-order reference model M(s) = m/(s+m)*eye(3) ###
        # M_i(s) = m_i/(s+m_i), i = x,y,z
#        self.A_m = np.diag(np.array([-15.0, -15.0, -25.0])) # USE THIS FOR L1 OUTPUT VELOCITY >> first order C
        mxy = -1.8
        self.A_m = np.diag(np.array([mxy, mxy, -1.95])) # THIRD ORDER Low Pass Filter

        ### Proportional position controller
        pxy = 0.15
        self.Pgain = np.array([[pxy],[pxy],[0.4]]) # THIRD ORDER
      
      else:
        ###### NOTE: TYPE 2: REAL SYSTEM PARAMETERS ARE HERE ######
        print "\n Running on Real System \n"
        print "L1 x-y-z translational velocity controller with projection based adaptation"
        
        if self.LPF_type ==3:
          ########## 2016 - 06 - 29 stable z (DO NOT TOUCH z PARAMS) AR.Drone 2_40
          
          ### L1 low pass filter cutoff frequency
          omxy = 1.55
          self.omega_cutoff = np.diag( np.array( [omxy, omxy, 1.45] ) ) # NOTE: EXPERIMENTS
          
          ### Reference Model -- first-order reference model M(s) = m/(s+m)*eye(3)  ###  M_i(s) = m_i/(s+m_i), i = x,y,z
          mxy = -2.2
#          self.A_m = np.diag(np.array([-15.0, -15.0, -25.0])) # FIRST ORDER Low Pass Filter
          self.A_m = np.diag(np.array([mxy, mxy, -1.65])) # THIRD ORDER Low Pass Filter
        
        else:
          ### L1 low pass filter cutoff frequency
          omxy = 1.55
          self.omega_cutoff = np.diag( np.array( [omxy, omxy, 1.35] ) )
          
          ### Reference Model -- first-order reference model M(s) = m/(s+m)*eye(3)  ###  M_i(s) = m_i/(s+m_i), i = x,y,z
          mxy = -2.77
          self.A_m = np.diag(np.array([mxy, mxy, -1.9])) # FIRST ORDER Low Pass Filter

        ### Proportional position controller
        pxy = 0.5
        self.Pgain = np.array([[pxy],[pxy],[0.35]])

      self.B_m = -self.A_m
    
      ### Create CSV file to keep a record of the parameters that produced the recorded results
      with open(self.save_dir + self.current_time + 'l1_experiment_info.csv','ab') as l1_info:
        writer = csv.writer(l1_info)
        writer.writerow(['L1_type', 'omega_cx', 'omega_cy', 'omega_cz', 'm_x', 'm_y', 'm_z', 'Pgain_x', 'Pgain_y', 'Pgain_z', 'N/A', 'N/A', 'N/A', 'Gamma', 'LPF_type'])
        writer.writerow(np.array( [self.L1_type, self.omega_cutoff[0][0], self.omega_cutoff[1][1], self.omega_cutoff[2][2], self.A_m[0][0], self.A_m[1][1], self.A_m[2][2], self.Pgain[0][0], self.Pgain[1][0], self.Pgain[2][0], 0,0,0, self.Gamma, self.LPF_type ] ))


    elif self.L1_type == 3:
      print "L1 roll-pitch angle, z velocity controller with projection based adaptation"

      ### L1 low pass filter cutoff frequency
      #self.omega_cutoff = np.diag( np.array( [8.0, 8.0, 6.75] ) ) # low-level third order
      self.omega_cutoff = np.diag( np.array( [2.0, 2.0, 2.0] ) ) # EXPERIMENTS

      pxy = 0.65
      self.Pgain = np.array([[pxy],[pxy],[0.25]]) # low level - third order - P only
#      self.Pgain = np.array([[0.2],[0.03],[1.00]]) # low level - third order - PD
      self.Dgain = 0.35*np.array([[0.06],[0.06],[0.0]]) # low level - third order

      ### L1 adaptive estimation ###
      self.Gamma = 80.0 # L1 adaptive gain (80 is good for z-direction) # third order
#      self.Gamma = np.array([[100.0], [100.0], [80.0]]) # L1 adaptive gain # low-level third order
    
      ### Projection Operator - convex set ###
      self.sigma_hat_max = 30.0 # maximum absolute nominal value of sigma_hat
      self.epsilon_sigma = 0.1 # tolerance on maximum sigma_hat

      ### Reference Model -- first-order reference model M(s) = m/(s+m)*eye(3) ###
      # M_i(s) = m_i/(s+m_i), i = x,y,z
      # A_m = diag(-mx -my -mz), B_m = diag(mx my mz)
      self.A_m = np.diag(np.array([-10.0, -10.0, -2.0])) # L1 low level - third order C
      self.B_m = -self.A_m

      ### Create CSV file to keep a record of the parameters that produced the recorded results
      with open(self.save_dir + self.current_time + 'l1_experiment_info.csv','ab') as l1_info:
        writer = csv.writer(l1_info)
        writer.writerow(['L1_type', 'omega_cx', 'omega_cy', 'omega_cz', 'm_x', 'm_y', 'm_z', 'Pgain_x', 'Pgain_y', 'Pgain_z', 'Dgain_x', 'Dgain_y', 'Dgain_z', 'Gamma'])
        writer.writerow(np.array( [self.L1_type, self.omega_cutoff[0][0], self.omega_cutoff[1][1], self.omega_cutoff[2][2], self.A_m[0][0], self.A_m[1][1], self.A_m[2][2], self.Pgain[0][0], self.Pgain[1][0], self.Pgain[2][0], self.Dgain[0][0], self.Dgain[1][0], self.Dgain[2][0], self.Gamma ] ))
      
    elif self.L1_type == 4:

      # L1 parameter adaptation
      self.Gamma = 1200.0

      ### Projection Operator - convex set ###
      self.sigma_hat_max = 200.0 # maximum absolute nominal value of sigma_hat
      self.epsilon_sigma = 0.1 # tolerance on maximum sigma_hat

      self.medfilter_old = np.array([[0.0],[0.0],[0.0]])
      self.medfilter_oldold = np.array([[0.0],[0.0],[0.0]])

      self.use_angle_pd = False

      self.LPF_type = 1

      # Check if running in simulation or on real system
      if self.simulation_flag:
        print "\n !!!!!  RUNNING IN SIMULATION !!!!!\n"
        print "L1 augmented standard nonlinear controller with projection based adaptation"
        # Gamma = 1200, 100, 100 
        ### L1 low pass filter cutoff frequency
        omxy = 0.9
        self.omega_cutoff = np.diag( np.array( [omxy, omxy, 2.0] ) ) # NOTE: SIMULATION

        ### Reference Model -- first-order reference model M(s) = m/(s+m)*eye(3)  ###  M_i(s) = m_i/(s+m_i), i = x,y,z
        mxy = -0.8
        self.A_m = np.diag(np.array([mxy, mxy, -1.7])) # THIRD ORDER Low Pass Filter

        self.P_L1_correction = 0.35#1.25#(self.tau_x**2)*0.05
        self.D_L1_correction = 0.3#1.0#self.tau_x/(2.0*self.zeta)*0.05	
        self.P_z_L1_correction = (self.tau_z**2)*0.4
      
      else:
        ###### NOTE: TYPE 4: REAL SYSTEM PARAMETERS ARE HERE ######
        print "\n Running on Real System \n"
        print "L1 augmented standard nonlinear controller with projection based adaptation"
        

        if self.LPF_type ==3:
          ### L1 low pass filter cutoff frequency
          omxy = 0.9
          self.omega_cutoff = np.diag( np.array( [omxy, omxy, 1.5] ) ) # NOTE: SIMULATION

          ### Reference Model -- first-order reference model M(s) = m/(s+m)*eye(3)  ###  M_i(s) = m_i/(s+m_i), i = x,y,z
          mxy = -0.8
          self.A_m = np.diag(np.array([mxy, mxy, -2.0])) # THIRD ORDER Low Pass Filter

          self.P_L1_correction = 1.25#(self.tau_x**2)
          self.D_L1_correction = 1.0#self.tau_x/(2.0*self.zeta)
          self.P_z_L1_correction = 1.05#(self.tau_z**2)
        
        else:
          ### L1 low pass filter cutoff frequency FIRST ORDER
          omxy = 0.9
          self.omega_cutoff = np.diag( np.array( [omxy, omxy, 1.5] ) ) # NOTE: SIMULATION

          ### Reference Model -- first-order reference model M(s) = m/(s+m)*eye(3)  ###  M_i(s) = m_i/(s+m_i), i = x,y,z
          mxy = -1.9
          self.A_m = np.diag(np.array([mxy, mxy, -2.0])) # FIRST ORDER Low Pass Filter

          self.P_L1_correction = 1.0#(self.tau_x**2)
          self.D_L1_correction = 0.8#self.tau_x/(2.0*self.zeta)
          self.P_z_L1_correction = 1.05#(self.tau_z**2)



        #self.P_L1_correction = 0.3
        #self.D_L1_correction = 0.1
        #self.P_z_L1_correction = 0.99
        
      self.B_m = -self.A_m

#      print "L1 augmented standard nonlinear controller with projection based adaptation"
#
#      ### L1 low pass filter cutoff frequency
#      #self.omega_cutoff = np.diag( np.array( [8.0, 8.0, 6.75] ) ) # low-level third order
#      self.omega_cutoff = np.diag( np.array( [2.0, 2.0, 2.0] ) ) # EXPERIMENTS
#
#      ### L1 adaptive estimation ###
#      self.Gamma = 800.0 # L1 adaptive gain (80 is good for z-direction) # third order
##      self.Gamma = np.array([[100.0], [100.0], [80.0]]) # L1 adaptive gain # low-level third order
#    
#      ### Projection Operator - convex set ###
#      self.sigma_hat_max = 30.0 # maximum absolute nominal value of sigma_hat
#      self.epsilon_sigma = 0.1 # tolerance on maximum sigma_hat
#
#      ### Reference Model -- first-order reference model M(s) = m/(s+m)*eye(3) ###
#      # M_i(s) = m_i/(s+m_i), i = x,y,z
#      # A_m = diag(-mx -my -mz), B_m = diag(mx my mz)
#      self.A_m = np.diag(np.array([-5.0, -5.0, -2.0])) # L1 low level - third order C
#      self.B_m = -self.A_m

      ### Create CSV file to keep a record of the parameters that produced the recorded results
      with open(self.save_dir + self.current_time + 'l1_experiment_info.csv','ab') as l1_info:
        writer = csv.writer(l1_info)
        writer.writerow(['L1_type', 'omega_cx', 'omega_cy', 'omega_cz', 'm_x', 'm_y', 'm_z', 'Pgain_x', 'Pgain_y', 'Pgain_z', 'Dgain_x', 'Dgain_y', 'Dgain_z', 'Gamma', 'Simulation'])
        writer.writerow(np.array( [self.L1_type, self.omega_cutoff[0][0], self.omega_cutoff[1][1], self.omega_cutoff[2][2], self.A_m[0][0], self.A_m[1][1], self.A_m[2][2], self.P_L1_correction* (1.0/self.tau_x**2), self.P_L1_correction*(1.0/self.tau_x**2), self.P_z_L1_correction*(1.0/self.tau_z**2), self.D_L1_correction*(2.0*self.zeta)/self.tau_x, self.D_L1_correction*(2.0*self.zeta)/self.tau_x, 0, self.Gamma, self.simulation_flag ] ))
      
      
    else:
      print "Standard DSL PD nonlinear controller"
      # No L1 parameters to define#

      ### Create CSV file to keep a record of the parameters that produced the recorded results
      with open(self.save_dir + self.current_time + 'l1_experiment_info.csv','ab') as l1_info:
        writer = csv.writer(l1_info)
        writer.writerow(['L1_type'])
        writer.writerow(np.array( [self.L1_type] ))
      

    print "created csv data file"

    ###########################################################################

    print 'tau_x: ', self.tau_x, '\n', 'tau_y: ', self.tau_y, '\n', 'tau_z: ', self.tau_z, '\n', 'tau_w: ', self.tau_w, '\n', 'zeta: ', self.zeta, '\n'
    print 'euler_angle_max: ', self.max_euler, '\n', 'control_vz_max: ', self.max_vz, '\n', 'control_yaw: ', self.max_yaw, '\n' 
    
    #print 'L1 Adaptive Output control parameters \n', 'Adaptive Gain:',self.Gamma,'\n', 'sigma_hat_max:',self.sigma_hat_max,'\n', 'epsilon_sigma:',self.epsilon_sigma,'\n', 'cutoff frequency:',self.omega_cutoff,'\n'

    # Requests path waypoint
    self.pub_request  = rospy.Publisher('waypoint_request', Bool, queue_size = 2)

    # Establish a timer to request waypoints at a given frequency
    self.waypointTimer  = rospy.Timer(rospy.Duration(self.COMMAND_PERIOD), self.requestWaypoint)

    # Signal Handling in case ROS is shut down (video display closed or <Ctrl-C>)
    rospy.on_shutdown(self.hook)
    rospy.loginfo('Nonlinear controller ready-----------------------------------')

  #*****************************************************************************

  # signal handeling in case of shutdown
  def hook(self):
    self.pubLand.publish(Empty())
    print "Landing!"

  #*****************************************************************************

  def requestWaypoint(self,event):
    self.pub_request.publish(self.status.request_waypoint)

  #*****************************************************************************
  def determineCommands(self):

    # Save variables so they are not over-written in mid-calculation
    des  = State.fromState(self.desired_state)
    curr = State.fromState(self.current_state)

    ardrone_rpy = self.navdata_rpy
    
    # calculate time since last determineCommand call for integration purposes
    now = rospy.get_rostime()
    dt = now.secs-self.oldtime.secs + (now.nsecs-self.oldtime.nsecs)*0.000000001

    if dt == 0 or dt > 0.1:
      #print 'now: ', now.secs, ' ', now.nsecs*0.000000001, '\n', 'old: ', self.oldtime.secs, ' ', self.oldtime.nsecs*0.000000001, '\n dt: ', dt
      if dt>0.1:
        print "####################      dt time longer than 0.1 sec       #################"
      dt = 0.001

    # store old time for next call
    self.oldtime = now
    
    '''
    ########################################################################
    # L1 Output Feedback Implementation 
    ########################################################################
    '''
    
    if self.L1_type == 3:
      # then use Projection based l1 output feedback on pitch, roll and z velocity
      
      ##### NOTE #####
      ##### x_L1_des = [ [phi_L1_des], [theta_L1_des], [z_dot_L1_des] ]
      ##### x_ref = [ [phi_ref], [theta_ref], [z_dot_ref] ]
      ##### 
      
      
      # first check whether drone is in flying mode
      # only use L1 when flying: 2 - landed, 6 - taking off, 3 - flying
      if (self.status.drone_state != 3): #and (self.status.drone_state !=7):
        # use standard controller when not in flying mode
        
        #self.x_L1_des = np.reshape(des.x, (3,-1))
        #y_tilde = np.array([[0.0],[0.0],[0.0]])
        
        # Z-velocity command m/sec)
        z_velocity_out =  ((2.0*self.zeta/self.tau_z) * (des.x_dot[2] - curr.x_dot[2]) + (1.0/(self.tau_z**2))*(des.x[2] - curr.x[2]) )
    
        # calculate the desired acceleration in x and y (global coordinates, [m/s^2] )
        ax = (2.0*self.zeta/self.tau_x)*(des.x_dot[0] - curr.x_dot[0]) + (1.0/(self.tau_x*self.tau_x))*(des.x[0]-curr.x[0])
        ay = (2.0*self.zeta/self.tau_x)*(des.x_dot[1] - curr.x_dot[1]) + (1.0/(self.tau_x*self.tau_x))*(des.x[1]-curr.x[1])
  
        # Roll/Pitch Commands
        # determine the mass-normalized thrust
        thrust = np.linalg.norm(np.array([0.,0.,self.g]) + curr.x_ddot)
  
        # keep ax,ay < thrust (so that arcsin is defined)
        if thrust == 0.0:
          print "########## thrust is zero ##########"
          ax_clamped = 1.0
          ay_clamped = 1.0
        else:
          ax_clamped = self.clamp(ax / thrust, 1.0)
          ay_clamped = self.clamp(ay / thrust, 1.0)
     
        # Rotate desired accelerations into drone's body frame
        ax_b =  ax_clamped*np.cos(curr.rpy[2]) + ay_clamped*np.sin(curr.rpy[2])
        ay_b = -ax_clamped*np.sin(curr.rpy[2]) + ay_clamped*np.cos(curr.rpy[2])
    
        ax_b = self.clamp(ax_b, 0.95)
        ay_b = self.clamp(ay_b, 0.95)

        # convert acceleration into roll/pitch angles [rad]
        pitch_out =  np.arcsin(ax_b)
        roll_out  = -np.arcsin(ay_b)


        
      else:

        # NOTE: TEST X-Y SYMMETRY
        if self.symmetry_check:
          curr.rpy = np.array([curr.rpy[1], curr.rpy[0], curr.rpy[2]])
          des.x = np.array([des.x[1], des.x[0], des.x[2]])
          curr.x = np.array([curr.x[1], curr.x[0], curr.x[2]])

        # use L1 control when in flying mode
        # calculate error between actual and reference state position
        y_tilde = self.x_ref - np.array( [ [curr.rpy[0]], [curr.rpy[1]], [curr.x_dot[2]] ] )
        
        ### Projection Operator to update sigma_hat based on y_tilde            ###
        f = ((self.epsilon_sigma + 1.0)*(self.sigma_hat.T.dot( self.sigma_hat )[0][0] ) - self.sigma_hat_max**2)/(self.epsilon_sigma*self.sigma_hat_max**2)
        grad_f = 2.0*(self.epsilon_sigma + 1.0)/(self.epsilon_sigma*self.sigma_hat_max**2)*self.sigma_hat
    
        if f<0:
          projection_result = -y_tilde
        else:
          if -grad_f.T.dot(y_tilde)[0][0] <0:
            projection_result = -y_tilde
          else:
            projection_result = -y_tilde + (1/np.linalg.norm(grad_f))*(grad_f)*grad_f.T.dot(y_tilde)[0][0]*f
      
        # multiply by adaptive Gain and integrate 
        sigma = self.sigma_hat + dt*(self.Gamma*projection_result)
        
        sigma_x = self.clamp(sigma[0][0], self.sigma_hat_max*(1+self.epsilon_sigma) )
        sigma_y = self.clamp(sigma[1][0], self.sigma_hat_max*(1+self.epsilon_sigma) )
        sigma_z = self.clamp(sigma[2][0], self.sigma_hat_max*(1+self.epsilon_sigma) )
        self.sigma_hat = np.array([[sigma_x],[sigma_y],[sigma_z]])
        #self.sigma_hat = self.clamp(self.sigma_hat + dt*(-self.Gamma*y_tilde), self.sigma_hat_max)
          
        # find desired x-y accelerations and z velocity from proportional-derivative controller
        # desired_acc = [[ax_desired],[ay_desired],[zdot_desired]]
        pos_error = np.reshape(des.x, (3,-1)) - np.reshape(curr.x, (3,-1))
        desired_acc = self.Pgain*( pos_error ) + self.Dgain*(1/dt)*(self.old_pos_error - pos_error)
        self.old_pos_error = pos_error

        # zdot_desired is unchanged (note that desired_acc[2] is actually zdot_des)
        self.desired_vel[2][0] = desired_acc[2][0]


        ### rotate and transform desired x-y accelerations in inertial frame into desired roll-pitch angles in body frame ###

        # determine the mass-normalized thrust
        thrust = np.linalg.norm(np.array([0.,0.,self.g]) + curr.x_ddot)
        
        # keep ax,ay < thrust (so that arcsin is defined)
        if thrust == 0.0:
          print "########## thrust is zero ##########"
          ax_clamped = 1.0
          ay_clamped = 1.0
        else:
          ax_clamped = self.clamp(desired_acc[0][0] / thrust, 1.0)
          ay_clamped = self.clamp(desired_acc[1][0] / thrust, 1.0)
        
        # Rotate desired accelerations into drone's body frame
        # NOTE: TEST X-Y SYMMETRY
        if self.symmetry_check:
          print '############ SYMMETRY CHECK REQUEST FAILED (requires rereversal to be implemented) #############'
          self.symmetry_check = 0

        if self.symmetry_check:
          ax_b =  ay_clamped*np.cos(curr.rpy[2]) + ax_clamped*np.sin(curr.rpy[2])
          ay_b = -ay_clamped*np.sin(curr.rpy[2]) + ax_clamped*np.cos(curr.rpy[2])

#          # reverse positions back for plotting reasons (otherwise this is plotted in reverse)
#          curr.rpy = np.array([curr.rpy[1], curr.rpy[0], curr.rpy[2]])
#          des.x = np.array([des.x[1], des.x[0], des.x[2]])
#          curr.x = np.array([curr.x[1], curr.x[0], curr.x[2]])

        else:
          # DEFAULT
          ax_b =  ax_clamped*np.cos(curr.rpy[2]) + ay_clamped*np.sin(curr.rpy[2])
          ay_b = -ax_clamped*np.sin(curr.rpy[2]) + ay_clamped*np.cos(curr.rpy[2])
     
        ax_b = self.clamp(ax_b, 0.95)
        ay_b = self.clamp(ay_b, 0.95)

        # convert body frame x-y accelerations into body frame roll-pitch angles [rad]
        self.desired_vel[1][0] =  np.arcsin(ax_b) # pitch
        self.desired_vel[0][0]  = -np.arcsin(ay_b) # roll




       
        ### Find revised desired roll-pitch-zdot by low-pass filtering the error ###
        #track_error = np.reshape(des.x_dot, (3,-1)) - self.sigma_hat
        track_error = self.desired_vel - self.sigma_hat  
        
###	Third Order Low Pass Filter y = C(s)*u
        # low pass filter C(s) = (3*omega_cutoff^2*s + omega_cutoff^3)/(s^3 + 3*omega_cutoff*s^2 + 3*omega_cutoff^2*s + omega_cutoff^3)
        
        # first find derivative of input signal (i.e. u = track_error, u_dot = d/dt(track_error) )
        self.u_dot = 1/dt*(track_error - self.u) # u_dot = 1/dt*(u - u_old)
        self.u = track_error # set current u to track_error (in next iteration, this is automatically u_old)
      
        self.y_ddot = self.y_ddot + dt*(-3*self.omega_cutoff.dot(self.y_ddot) - 3*(self.omega_cutoff**2).dot(self.y_dot) - (self.omega_cutoff**3).dot(self.y) + 3*(self.omega_cutoff**2).dot(self.u_dot) + (self.omega_cutoff**3).dot(self.u) )
        self.y_dot = self.y_dot + dt*(self.y_ddot)
        self.y = self.y + dt*(self.y_dot)
      
        # low pass filter output is L1 desired
        self.x_L1_des = self.y




        ### reference model -- M(s) = m/(s+m) -- x_ref = M(s)(u + sigma_hat) ###
        self.x_ref = self.x_ref + dt*self.B_m.dot( -self.x_ref + self.x_L1_des + self.sigma_hat )
        
        # append to csv file
        with open(self.save_dir + self.current_time + 'l1_ref_output.csv','ab') as ref_model:
          writer = csv.writer(ref_model)
          # time secs, time nsecs, x_ref(1:3), x_dot(1:3), sigma_hat(1:3), x_L1_des(1:3), x_dot_des(1:3), x(1:3), x_des(1:3), desired_acc(1:3), rpy(1:3)
          writer.writerow(np.array([now.secs, now.nsecs, self.x_ref[0][0], self.x_ref[1][0], self.x_ref[2][0], curr.x_dot[0], curr.x_dot[1], curr.x_dot[2], self.sigma_hat[0][0], self.sigma_hat[1][0], self.sigma_hat[2][0], self.x_L1_des[0][0], self.x_L1_des[1][0], self.x_L1_des[2][0], self.desired_vel[0][0], self.desired_vel[1][0], self.desired_vel[2][0], curr.x[0], curr.x[1], curr.x[2], des.x[0], des.x[1], des.x[2], desired_acc[0][0], desired_acc[1][0], desired_acc[2][0], curr.rpy[0], curr.rpy[1], curr.rpy[2]]))
      
      ###########################################################################

        z_velocity_out = self.x_L1_des[2][0]
        pitch_out =  self.x_L1_des[1][0]
        roll_out  = self.x_L1_des[0][0]

      #print dt

      # Yaw rate command (rad/sec)??
      yaw_err = np.mod(des.rpy[2]-curr.rpy[2] + np.pi, 2.*np.pi) - np.pi
      yaw_velocity_out = (1.0 / self.tau_w) * yaw_err

      #########################################################################

      ### Check for valid outputs and LIMIT if necessary
      if np.fabs(pitch_out) > 0.75:
        print "pitch: ", pitch_out
        print "ax: ", ax
        print "ay: ", ay
        print "dt: ", dt
        pitch_out = np.sign(pitch_out)*0.75

      elif np.isnan(pitch_out):
        print "pitch is NaN before sendCommand ******************* ax_b: ", ax_b
        pitch_out = 0.0
      
      if np.fabs(roll_out) > 0.75:
        print "roll: ", roll_out
        print "ax: ", ax
        print "ay: ", ay
        print "dt: ", 
        roll_out = np.sign(roll_out)*0.75

      elif np.isnan(roll_out):
        print "roll is NaN before sendCommand ******************* ay_b: ", ay_b
        roll_out = 0.0
    

      #### send the commands to the drone if the keyboard is not currently being used
      if(self.status.keyboard_override == 0):
        self.status.t_last_cmd = time.time()
  
        if math.isnan(pitch_out):
          print "pitch is NaN before sendCommand"

        if math.isnan(roll_out):
          print "roll is NaN before sendCommand"
  
        if self.angles_log:
          with open(self.save_dir + self.current_time + 'l1_angles.csv','ab') as angles:
            writer = csv.writer(angles)
            writer.writerow(np.array([roll_out, pitch_out, yaw_velocity_out, z_velocity_out, curr.rpy[0], curr.rpy[1], curr.x_dot[2], now.secs, now.nsecs]))
        
        self.SendCommand(roll_out, pitch_out, yaw_velocity_out, z_velocity_out)

    
    
    elif self.L1_type == 4:
      '''
      #########################################################################
      # PROJECTION BASED L1 OUTPUT FEEDBACK on ROLL - PITCH - Z-DOT           #
      #########################################################################
      '''

      # then use L1 augmented standard nonlinear controller with projection based adaptation
      
      ##### NOTE #####
      ##### x_L1_des = [ [phi_L1_des], [theta_L1_des], [z_dot_L1_des] ]
      ##### x_ref = [ [phi_ref], [theta_ref], [z_dot_ref] ]
      ##### 

      ##### Use DSL Corrected PD Controller on outer loop

      # calculate the desired acceleration in x and y (global coordinates, [m/s^2] )
      pos_error = np.reshape(des.x, (3,-1)) - np.reshape(curr.x, (3,-1))


      #ax = -(2.0*self.zeta/self.tau_x)*(1/dt)*self.D_L1_correction*(self.old_pos_error[0][0] - pos_error[0][0]) + (1.0/(self.tau_x*self.tau_x))*self.P_L1_correction*(pos_error[0][0])
      #ay = -(2.0*self.zeta/self.tau_x)*(1/dt)*self.D_L1_correction*(self.old_pos_error[1][0] - pos_error[1][0]) + (1.0/(self.tau_x*self.tau_x))*self.P_L1_correction*(pos_error[1][0])
      ax = -(2.0*self.zeta/self.tau_x)*self.D_L1_correction*(curr.x_dot[0]) + (1.0/(self.tau_x*self.tau_x))*self.P_L1_correction*(pos_error[0][0])
      ay = -(2.0*self.zeta/self.tau_x)*self.D_L1_correction*(curr.x_dot[1]) + (1.0/(self.tau_x*self.tau_x))*self.P_L1_correction*(pos_error[1][0])
      
      check_roll_D_term = self.old_pos_error[1][0] - pos_error[1][0]
      check_pitch_D_term = self.old_pos_error[0][0] - pos_error[0][0]
      self.old_pos_error = pos_error

      # Yaw rate command (rad/sec)
      yaw_err = np.mod(des.rpy[2]-curr.rpy[2] + np.pi, 2.*np.pi) - np.pi
      yaw_velocity_out = (1.0 / self.tau_w) * yaw_err
      
      # Determine the mass-normalized thrust
      thrust = np.linalg.norm(np.array([0.,0.,self.g]) + curr.x_ddot)
      
      # keep ax,ay < thrust (so that arcsin is defined)
      if thrust == 0.0:
        print "########## thrust is zero ##########"
        ax_clamped = 0.97
        ay_clamped = 0.97
      else:
        # Clamp thrust 
        ax_clamped = self.clamp(ax / thrust, 0.97)
        ay_clamped = self.clamp(ay / thrust, 0.97)
   
      # Rotate desired accelerations into drone's body frame
      ax_b = self.clamp( ax_clamped*np.cos(curr.rpy[2]) + ay_clamped*np.sin(curr.rpy[2]), 0.97)
      ay_b = self.clamp(-ax_clamped*np.sin(curr.rpy[2]) + ay_clamped*np.cos(curr.rpy[2]), 0.97)
      
      # convert acceleration into roll/pitch angles [rad]
      roll_des  = -np.arcsin(ay_b)
      pitch_des =  np.arcsin(ax_b)
      
      zdot_des = (1.0/(self.tau_z**2))*self.P_z_L1_correction*( pos_error[2][0] )

      # First check whether drone is in flying mode
      #       only use L1 when flying: 2 - landed, 3 - flying, 4 - hover, 6 - taking off, 7 - hover
      if self.start_flight_timer:
        duration = now.secs - self.start_time
        if duration >= self.delay_until_L1_start:
           self.print_L1_status = True
           if self.print_L1_status == self.print_L1_status_flag:
             print "\nL1 control has taken over\n"
             self.print_L1_status_flag = False
      else:
        duration = 0

      if (self.status.drone_state != 3) or duration < self.delay_until_L1_start:
        # use standard controller when not in flying mode
        
        # Z-velocity command m/sec)
        z_velocity_out =  zdot_des

        # Roll - Pitch angle commands (rad/sec)
        roll_out  = roll_des
        pitch_out = pitch_des
        
      else:
        # Then use Projection based l1 output feedback on pitch, roll and z velocity

        # NOTE: TEST X-Y SYMMETRY
        if self.symmetry_check:
          curr.rpy = np.array([curr.rpy[1], curr.rpy[0], curr.rpy[2]])
          des.x = np.array([des.x[1], des.x[0], des.x[2]])
          curr.x = np.array([curr.x[1], curr.x[0], curr.x[2]])
        
        
        # Calculate error between actual and reference state position
#        y_tilde = self.x_ref - np.array( [ [curr.rpy[0]], [curr.rpy[1]], [curr.x_dot[2]] ] )
        y_tilde = self.x_ref - np.array( [ [ardrone_rpy[0][0]], [ardrone_rpy[1][0]], [curr.x_dot[2]] ] )
        
        ### Projection Operator to update sigma_hat based on y_tilde            ###
        f = ((self.epsilon_sigma + 1.0)*(self.sigma_hat.T.dot( self.sigma_hat )[0][0] ) - self.sigma_hat_max**2)/(self.epsilon_sigma*self.sigma_hat_max**2)
        grad_f = 2.0*(self.epsilon_sigma + 1.0)/(self.epsilon_sigma*self.sigma_hat_max**2)*self.sigma_hat
    
        if f<0:
          projection_result = -y_tilde
        else:
          if -grad_f.T.dot(y_tilde)[0][0] <0:
            projection_result = -y_tilde
          else:
            projection_result = -y_tilde + (1/np.linalg.norm(grad_f))*(grad_f)*grad_f.T.dot(y_tilde)[0][0]*f
      
        # multiply by adaptive Gain and integrate 
        sigma = self.sigma_hat + dt*(np.array([[1],[1],[0.1]])*self.Gamma*projection_result)
        
        # hard clamp in case of projection issues
        sigma_x = self.clamp(sigma[0][0], self.sigma_hat_max*(1+self.epsilon_sigma) )
        sigma_y = self.clamp(sigma[1][0], self.sigma_hat_max*(1+self.epsilon_sigma) )
        sigma_z = self.clamp(sigma[2][0], self.sigma_hat_max*(1+self.epsilon_sigma) )
        
        if (sigma[0][0] > sigma_x) or (sigma[1][0] > sigma_y) or (sigma[2][0] > sigma_z):
          print "sigma clamp used"

        self.sigma_hat = np.array([[sigma_x],[sigma_y],[sigma_z]])
        


        L1_input = np.array([[roll_des], [pitch_des], [zdot_des]])

        # Removing spikes in the signal (1-D, 3rd order median filter)
        self.desired_vel = np.median(np.array([self.medfilter_oldold, self.medfilter_old, L1_input]), axis=0)
        self.medfilter_oldold = self.medfilter_old
        self.medfilter_old = L1_input
        
        
        ### Find revised desired roll-pitch-zdot by low-pass filtering the error ###
        track_error = self.desired_vel - self.sigma_hat
        #track_error = np.diag(np.array([-1,-1,1])).dot(track_error)


        if self.LPF_type == 1:
          ###	First Order Low Pass Filter
          # low pass filter C(s) = omega_cutoff / (s + omega_cutoff) ### NOTE: decoupled directions
          self.x_L1_des = self.x_L1_des + dt*self.omega_cutoff.dot( -self.x_L1_des + track_error )

        elif self.LPF_type == 3:
          #### Third Order Low Pass Filter y = C(s)*u
          self.u_dot[0][0] = 1/dt*(track_error[0][0] - self.u[0][0]) # u_dot = 1/dt*(u - u_old)
          self.u_dot[1][0] = 1/dt*(track_error[1][0] - self.u[1][0]) # u_dot = 1/dt*(u - u_old)
          self.u_dot[2][0] = 1/dt*(track_error[2][0] - self.u[2][0]) # u_dot = 1/dt*(u - u_old)

          self.u = track_error # set current u to track_error (in next iteration, this is automatically u_old)
    
          self.y_ddot[0][0] = self.y_ddot[0][0] + dt*(-3*self.omega_cutoff[0][0]*(self.y_ddot[0][0]) - 3*(self.omega_cutoff[0][0]**2)*(self.y_dot[0][0]) - (self.omega_cutoff[0][0]**3)*(self.y[0][0]) + 3*(self.omega_cutoff[0][0]**2)*(self.u_dot[0][0]) + (self.omega_cutoff[0][0]**3)*(self.u[0][0]) )

          self.y_ddot[1][0] = self.y_ddot[1][0] + dt*(-3*self.omega_cutoff[1][1]*(self.y_ddot[1][0]) - 3*(self.omega_cutoff[1][1]**2)*(self.y_dot[1][0]) - (self.omega_cutoff[1][1]**3)*(self.y[1][0]) + 3*(self.omega_cutoff[1][1]**2)*(self.u_dot[1][0]) + (self.omega_cutoff[1][1]**3)*(self.u[1][0]) )

          self.y_ddot[2][0] = self.y_ddot[2][0] + dt*(-3*self.omega_cutoff[2][2]*(self.y_ddot[2][0]) - 3*(self.omega_cutoff[2][2]**2)*(self.y_dot[2][0]) - (self.omega_cutoff[2][2]**3)*(self.y[2][0]) + 3*(self.omega_cutoff[2][2]**2)*(self.u_dot[2][0]) + (self.omega_cutoff[2][2]**3)*(self.u[2][0]) )


          self.y_dot[0][0] = self.y_dot[0][0] + dt*(self.y_ddot[0][0])
          self.y_dot[1][0] = self.y_dot[1][0] + dt*(self.y_ddot[1][0])
          self.y_dot[2][0] = self.y_dot[2][0] + dt*(self.y_ddot[2][0])

          self.y[0][0] = self.y[0][0] + dt*(self.y_dot[0][0])
          self.y[1][0] = self.y[1][0] + dt*(self.y_dot[1][0])
          self.y[2][0] = self.y[2][0] + dt*(self.y_dot[2][0])
        
####	Third Order Low Pass Filter y = C(s)*u
##        # low pass filter C(s) = (3*omega_cutoff^2*s + omega_cutoff^3)/(s^3 + 3*omega_cutoff*s^2 + 3*omega_cutoff^2*s + omega_cutoff^3)
##        
##        # first find derivative of input signal (i.e. u = track_error, u_dot = d/dt(track_error) )
#        self.u_dot = 1/dt*(track_error - self.u) # u_dot = 1/dt*(u - u_old)
#        self.u = track_error # set current u to track_error (in next iteration, this is automatically u_old)
#       
#        self.y_ddot = self.y_ddot + dt*(-3*self.omega_cutoff.dot(self.y_ddot) - 3*(self.omega_cutoff**2).dot(self.y_dot) - (self.omega_cutoff**3).dot(self.y) + 3*(self.omega_cutoff**2).dot(self.u_dot) + (self.omega_cutoff**3).dot(self.u) )
#        self.y_dot = self.y_dot + dt*(self.y_ddot)
#        self.y = self.y + dt*(self.y_dot)
        
          # low filter output is L1 desired velocity
          self.x_L1_des = self.y

        else:
          print "\n !!! NO FILTER TYPE !!! reverting to 1st order LPF"
          self.x_L1_des = self.x_L1_des + dt*self.omega_cutoff.dot( -self.x_L1_des + track_error )
        
        
        ### Reference model -- M(s) = m/(s+m) -- x_ref = M(s)(u + sigma_hat) ###
        self.x_ref = self.x_ref + dt*self.B_m.dot( -self.x_ref + self.x_L1_des + self.sigma_hat )
        
        
        ### Log to csv file
        with open(self.save_dir + self.current_time + 'std_ctrl_augmented_L1.csv','ab') as ref_model:
          writer = csv.writer(ref_model)
          #time secs, time nsecs, x_ref(1:3), x_dot(1:3), sigma_hat(1:3), x_L1_des(1:3), x_dot_des(1:3), x(1:3), x_des(1:3)
          writer.writerow(np.array([now.secs, now.nsecs, self.x_ref[0][0], self.x_ref[1][0], self.x_ref[2][0], curr.x_dot[0], curr.x_dot[1], curr.x_dot[2], self.sigma_hat[0][0], self.sigma_hat[1][0], self.sigma_hat[2][0], self.x_L1_des[0][0], self.x_L1_des[1][0], self.x_L1_des[2][0], self.desired_vel[0][0], self.desired_vel[1][0], self.desired_vel[2][0], curr.x[0], curr.x[1], curr.x[2], des.x[0], des.x[1], des.x[2], 0,0,0, curr.rpy[0], curr.rpy[1], curr.rpy[2], ardrone_rpy[0][0], ardrone_rpy[1][0], ax_b, ay_b]))
        

        if not(self.use_angle_pd):
          ### Send commands
          z_velocity_out = self.x_L1_des[2][0]
          pitch_out =  self.x_L1_des[1][0]
          roll_out  = self.x_L1_des[0][0]

        else:
          ### PD controller in roll-pitch below L1
          z_velocity_out = self.x_L1_des[2][0]
          pitch_out_L1 =  self.x_L1_des[1][0]
          roll_out_L1  = self.x_L1_des[0][0]
    
          rp_P_gain = 1.7
          rp_D_gain = 0.3
          rp_error = np.array([[(roll_out_L1 - curr.rpy[0])],[pitch_out_L1 - curr.rpy[1]]])
          
          roll_out = self.clamp( rp_P_gain*(rp_error[0][0]) - rp_D_gain*(1/dt)*(self.old_rp_error[0][0] - rp_error[0][0]), 0.75)
          pitch_out = self.clamp( rp_P_gain*(rp_error[1][0]) - rp_D_gain*(1/dt)*(self.old_rp_error[1][0] - rp_error[1][0]), 0.75)
    
          self.old_rp_error = rp_error
        
      
      #########################################################################

      ### Check for valid outputs and LIMIT if necessary
      if np.fabs(pitch_out) > 0.75:
        print "pitch: ", pitch_out, "   ax: ", ax, "   ay: ", ay, "   dt: ", dt
        print "pitch D term: ", check_pitch_D_term
        pitch_out = np.sign(pitch_out)*0.75

      elif np.isnan(pitch_out):
        print "pitch is NaN before sendCommand -- ax_b: ", ax_b
        pitch_out = 0.0
      
      if np.fabs(roll_out) > 0.75:
        print "roll: ", roll_out, "   ax: ", ax, "   ay: ", ay, "   dt: ", dt
        print "roll D term: ", check_roll_D_term
        roll_out = np.sign(roll_out)*0.75

      elif np.isnan(roll_out):
        print "roll is NaN before sendCommand -- ay_b: ", ay_b
        roll_out = 0.0

      #### send the commands to the drone if the keyboard is not currently being used
      if(self.status.keyboard_override == 0):
        self.status.t_last_cmd = time.time()
  
        if self.angles_log:
          with open(self.save_dir + self.current_time + 'l1_angles.csv','ab') as angles:
            writer = csv.writer(angles)
            writer.writerow(np.array([roll_out, pitch_out, yaw_velocity_out, z_velocity_out, curr.rpy[0], curr.rpy[1], curr.x_dot[2], now.secs, now.nsecs]))
        
        self.SendCommand(roll_out, pitch_out, yaw_velocity_out, z_velocity_out)

###############################################################################

###############################################################################      
    else:
      #print "starting higher level L1 output feedback controller"
      # use higher level L1 controllers
      
      if self.L1_type == 1:
        # then use Piecewise Constant l1 output feedback on position
        # first check whether drone is in flying mode
        
        # only use L1 when flying: 2 - landed, 6 - taking off, 3 - flying
        if self.status.drone_state != 3:
          self.x_L1_des = np.reshape(des.x, (3,-1))
          y_tilde = np.array([[0.0],[0.0],[0.0]])
          #print self.x_L1_des
          #print 'no L1, start flying'
        else:
          # use L1 adaptive output
          
          # calculate error between actual and reference state position
          y_tilde = self.x_ref - np.reshape(curr.x, (3,-1))
          #self.pubYtilde_x.publish(self.y_tilde[0][0])
          #self.pubYtilde_y.publish(self.y_tilde[1][0])
          #self.pubYtilde_z.publish(self.y_tilde[2][0])
        
          # ensure dt is not 0
          if dt <= 0.0001:
            #print 'dt min reached: ', dt
            dt = 0.001
            
          #m = 20.0
          #k = 0.5
#          m = 25.0
#          k = 0.8
          
          self.sigma_hat[0][0] = -1.0/self.B_m[0][0] * ( 1.0 / (math.exp(self.A_m[0][0]*dt) - 1.0) ) * (1.0/self.A_m[0][0]) * math.exp(self.A_m[0][0]*dt) * y_tilde[0][0]
          self.sigma_hat[1][0] = -1.0/self.B_m[1][1] * ( 1.0 / (math.exp(self.A_m[1][1]*dt) - 1.0) ) * (1.0/self.A_m[1][1]) * math.exp(self.A_m[1][1]*dt) * y_tilde[1][0]
          self.sigma_hat[2][0] = -1.0/self.B_m[2][2] * ( 1.0 / (math.exp(self.A_m[2][2]*dt) - 1.0) ) * (1.0/self.A_m[2][2]) * math.exp(self.A_m[2][2]*dt) * y_tilde[2][0]
  
          
#          eta_hat = 1.0*self.x_L1_des[0][0] + self.sigma_hat[0][0] - des.x[0]
#  
#          self.x_L1_des[0][0] = self.x_L1_des[0][0] + dt*(-k*eta_hat)
#          
#          self.x_ref[0][0] = self.x_ref[0][0] + dt*(m)*( -self.x_ref[0][0] + self.x_L1_des[0][0] + self.sigma_hat[0][0] )
          
          
#          ### Piecewise constant update of sigma_hat based on y_tilde ###
#          ### NOTE: Implementation currently assumes A_m diagonal
#          # calculate matrix exponential of A_m*T_s
#          Am_exp = np.diag(np.array([math.exp( self.A_m[0][0] * dt ), math.exp( self.A_m[1][1] * dt ), math.exp( self.A_m[2][2] * dt )]))
#          #print Am_exp[0][0] - 1, Am_exp[1][1] - 1, Am_exp[2][2] - 1
#          #print 'dt: ', dt
#          
#          # calculate Phi(T_s)
#          Phi_inv = np.diag(np.array( [ 1.0/(Am_exp[0][0] - 1.0), 1.0/(Am_exp[1][1] - 1.0), 1.0/(Am_exp[2][2] - 1.0) ] ))
#          #if np.isnan(1/(Am_exp[0][0] - 1)) or np.isinf(1/(Am_exp[0][0] - 1)):
#          #  print 1/(Am_exp[0][0] - 1)
#          # Phi_inv = np.linalg.inv(Am_exp - np.eye(3))
#          Phi_inv = Phi_inv.dot( self.A_m )
#          #print 'Phi_inv', '\n', Phi_inv, '\n' 
#        
#          # calculate sigma_hat
#          self.sigma_hat = -self.B_inv.dot( Phi_inv.dot( Am_exp.dot( y_tilde ) ) )
#          
#          #self.sigma_hat = np.array( [ [-self.B_inv[0][0]*Phi_inv[0][0]*Am_exp[0][0]*y_tilde[0][0]] , [-self.B_inv[1][1]*Phi_inv[1][1]*Am_exp[1][1]*y_tilde[1][0]] , [-self.B_inv[2][2]*Phi_inv[2][2]*Am_exp[2][2]*y_tilde[2][0]] ] )
#    
#          #sigma_x = self.clamp(self.sigma_hat[0][0],30)
#          #if np.isnan(sigma_x):
#          #  sigma_x = 0
#          #sigma_y = self.clamp(self.sigma_hat[1][0],30)
#          #if np.isnan(sigma_y):
#          #  sigma_y = 0
#          #sigma_z = self.clamp(self.sigma_hat[2][0],30)
#          #if np.isnan(sigma_z):
#          #  sigma_z = 0
#          #self.sigma_hat = np.array([[sigma_x],[sigma_y],[sigma_z]])
#          
#          #if np.isnan(self.sigma_hat[0][0]):
#          #  print 'sigma_hat', '\n', self.sigma_hat, '\n'
        
        
          ### Find revised x desired L1 ###
          
          # exact tracking: r_g = K_g * r, K_g = -inv(C*inv(Am)*Bm)
          # C = eye(3), diag Am = -Bm => K_g = eye(3)
          # -np.linalg.inv( np.eye(3).dot( (np.linalg.inv(A_m)).dot(B_m) ) )
          # r_g(t) = r(t)
          r_g = np.reshape(des.x, (3,-1))
          
          # calculate intermediate signal for adaptive ouput controller
          eta_hat = self.omega_0.dot( self.x_L1_des ) + self.sigma_hat - r_g
          #print 'eta_hat', '\n', eta_hat
          
          # calculate revised x position -- D(s) = 1/s, simple integrator
          self.x_L1_des = self.x_L1_des + dt*( -self.K.dot( eta_hat ) )
          #print self.x_L1_des
          
          
          ### reference model -- x_dot_ref = Am*x_ref + Bm(omega_0*x_L1_des + sigma_hat) ###
          self.x_ref = self.x_ref + dt*self.B_m.dot( -self.x_ref + self.x_L1_des + self.sigma_hat )
          
          # append to csv file
          with open(self.save_dir + self.current_time + 'l1_pos_output.csv','ab') as ref_model:
            writer = csv.writer(ref_model)
            # time secs, time nsecs, x_ref(1:3), x_dot(1:3), sigma_hat(1:3), x_L1_des(1:3), x_dot_des(1:3), x(1:3), x_des(1:3)
            #writer.writerow(np.array([now.secs, now.nsecs, self.x_ref[0][0], self.x_ref[1][0], self.x_ref[2][0], curr.x_dot[0], curr.x_dot[1], curr.x_dot[2], self.sigma_hat[0][0], self.sigma_hat[1][0], self.sigma_hat[2][0], self.x_L1_des[0][0], self.x_L1_des[1][0], self.x_L1_des[2][0], self.desired_vel[0][0], self.desired_vel[1][0], self.desired_vel[2][0], curr.x[0], curr.x[1], curr.x[2], des.x[0], des.x[1], des.x[2]]))
            # time secs, time nsecs, x_ref(1:3), x_dot(1:3), sigma_hat(1:3), x_L1_des(1:3), x_dot_des(1:3), x(1:3), x_des(1:3), desired_acc(1:3), rpy(1:3)
            writer.writerow(np.array([now.secs, now.nsecs, self.x_ref[0][0], self.x_ref[1][0], self.x_ref[2][0], curr.x_dot[0], curr.x_dot[1], curr.x_dot[2], self.sigma_hat[0][0], self.sigma_hat[1][0], self.sigma_hat[2][0], self.x_L1_des[0][0], self.x_L1_des[1][0], self.x_L1_des[2][0], self.desired_vel[0][0], self.desired_vel[1][0], self.desired_vel[2][0], curr.x[0], curr.x[1], curr.x[2], des.x[0], des.x[1], des.x[2], 0,0,0, curr.rpy[0], curr.rpy[1], curr.rpy[2]]))
  
          
        #print self.sigma_hat
        self.pubL1des_x.publish(self.x_L1_des[0][0])
        self.pubL1des_y.publish(self.x_L1_des[1][0])
        self.pubL1des_z.publish(self.x_L1_des[2][0])
    
        self.pubParama.publish(self.sigma_hat[0][0])
        self.pubParamb.publish(self.sigma_hat[1][0])
        self.pubParamc.publish(self.sigma_hat[2][0])
    
        self.pubXref_x.publish(self.x_ref[0][0])
        self.pubXref_y.publish(self.x_ref[1][0])
        self.pubXref_z.publish(self.x_ref[2][0])

        ###########################################################################

        # Z-velocity command m/sec)
        #z_velocity_out =  ((2.0*self.zeta/self.tau_z) * (des.x_dot[2] - curr.x_dot[2]) + (1.0/(self.tau_z**2))*(des.x[2] - curr.x[2]) )
        z_velocity_out = (1.0/(self.tau_z**2))*(self.x_L1_des[2][0] - curr.x[2]) ### NOTE: x_L1_des z-position
    
        # calculate the desired acceleration in x and y (global coordinates, [m/s^2] )
        #ax = (2.0*self.zeta/self.tau_x)*(0 - curr.x_dot[0]) + (1.0/(self.tau_x*self.tau_x))*(self.x_L1_des[0][0]-curr.x[0]) ### NOTE: x_L1_des x-position
        #ay = (2.0*self.zeta/self.tau_x)*(0 - curr.x_dot[1]) + (1.0/(self.tau_x*self.tau_x))*(self.x_L1_des[1][0]-curr.x[1]) ### NOTE: x_L1_des y-position
        #ax = (1.0/(self.tau_x*self.tau_x))*0.1*(self.x_L1_des[0][0]-curr.x[0]) ### NOTE: x_L1_des x-position
        #ax = (1.0/(self.tau_x*self.tau_x))*(des.x[0]-curr.x[0])
        #ay = (2.0*self.zeta/self.tau_x)*0.1*(des.x_dot[1] - curr.x_dot[1]) + (1.0/(self.tau_x*self.tau_x))*(des.x[1]-curr.x[1]) ### NOTE: x_L1_des y-position
  
        ax = (2.0*self.zeta/self.tau_x)*0.35*(1.0/dt)*(self.x_L1_des[0][0] - curr.x[0] - self.old_err[0][0]) + (1.0/(self.tau_x*self.tau_x))*1.5*(self.x_L1_des[0][0]-curr.x[0]) ### NOTE: x_L1_des x-position
        ay = (2.0*self.zeta/self.tau_x)*0.35*(1.0/dt)*(self.x_L1_des[1][0] - curr.x[1] - self.old_err[1][0]) + (1.0/(self.tau_x*self.tau_x))*1.5*(self.x_L1_des[1][0]-curr.x[1]) ### NOTE: x_L1_des y-position
        self.old_err = self.x_L1_des - np.reshape(curr.x, (3,-1))

        #ay = (2.0*self.zeta/self.tau_x)*(des.x_dot[1] - curr.x_dot[1]) + (1.0/(self.tau_x*self.tau_x))*(self.x_L1_des[1][0]-curr.x[1]) ### NOTE: x_L1_des y-position
        #ax = (1.0/(self.tau_x*self.tau_x))*(self.x_L1_des[0][0]-curr.x[0]) ### NOTE: x_L1_des x-position
        #ay = (1.0/(self.tau_x*self.tau_x))*(self.x_L1_des[1][0]-curr.x[1]) ### NOTE: x_L1_des y-position
      
      elif self.L1_type == 2:
        '''
        #########################################################################
        # PROJECTION BASED L1 OUTPUT FEEDBACK ON X-DOT                          #
        #########################################################################
        '''
        # then use Projection based l1 output feedback on translational velocity

        # Once takeoff has commenced, start timer and print when L1 has taken over
        if self.start_flight_timer:
          duration = now.secs - self.start_time
          if duration >= self.delay_until_L1_start:
             self.print_L1_status = True
             if self.print_L1_status == self.print_L1_status_flag:
               print "\nL1 control has taken over\n"
               self.print_L1_status_flag = False
        else:
          duration = 0

        # First check whether drone is in flying mode
        # Only use L1 when flying: 2 - landed, 6 - taking off, 3 - flying
        if (self.status.drone_state != 3) or duration < self.delay_until_L1_start:

          self.x_L1_des = np.reshape(des.x_dot, (3,-1))
          self.x_L1_des[2][0] = z_velocity_out = (1.0/(self.tau_z**2))*( des.x[2] - curr.x[2] )
          y_tilde = np.array([[0.0],[0.0],[0.0]])

        else:
  
          # calculate error between actual and reference state position
          y_tilde = self.x_ref - np.reshape(curr.x_dot, (3,-1))
          
          ### Projection Operator to update sigma_hat based on y_tilde            ###
          f = ((self.epsilon_sigma + 1.0)*(self.sigma_hat.T.dot( self.sigma_hat )[0][0] ) - self.sigma_hat_max**2)/(self.epsilon_sigma*self.sigma_hat_max**2)
          grad_f = 2.0*(self.epsilon_sigma + 1.0)/(self.epsilon_sigma*self.sigma_hat_max**2)*self.sigma_hat
      
          if f<0:
            projection_result = -y_tilde
          else:
            if -grad_f.T.dot(y_tilde)[0][0] <0:
              projection_result = -y_tilde
            else:
              projection_result = -y_tilde + (1/np.linalg.norm(grad_f))*(grad_f)*grad_f.T.dot(y_tilde)[0][0]*f
        
          # multiply by adaptive Gain and integrate 
          sigma = self.sigma_hat + dt*(np.array([[1],[1],[1]])*self.Gamma*projection_result)
          
          sigma_x = self.clamp(sigma[0][0], self.sigma_hat_max*(1+self.epsilon_sigma) )
          sigma_y = self.clamp(sigma[1][0], self.sigma_hat_max*(1+self.epsilon_sigma) )
          sigma_z = self.clamp(sigma[2][0], self.sigma_hat_max*(1+self.epsilon_sigma) )
          self.sigma_hat = np.array([[sigma_x],[sigma_y],[sigma_z]])
          #self.sigma_hat = self.clamp(self.sigma_hat + dt*(-self.Gamma*y_tilde), self.sigma_hat_max)
          #print self.sigma_hat
          
#          f_x = ((self.epsilon_sigma + 1.0)*(self.sigma_hat[0][0] * self.sigma_hat[0][0] ) - self.sigma_hat_max**2)/(self.epsilon_sigma*self.sigma_hat_max**2)
#          f_y = ((self.epsilon_sigma + 1.0)*(self.sigma_hat[1][0] * self.sigma_hat[1][0] ) - self.sigma_hat_max**2)/(self.epsilon_sigma*self.sigma_hat_max**2)
#          f_z = ((self.epsilon_sigma + 1.0)*(self.sigma_hat[2][0] * self.sigma_hat[2][0] ) - self.sigma_hat_max**2)/(self.epsilon_sigma*self.sigma_hat_max**2)
#          grad_f = 2.0*(self.epsilon_sigma + 1.0)/(self.epsilon_sigma*self.sigma_hat_max**2)*self.sigma_hat
#    
#          if f_x < 0:
  #          projection_result_x = -y_tilde[0][0]
#          else:
#            if -grad_f[0][0] * y_tilde[0][0] < 0:
#              projection_result_x = -y_tilde[0][0]
#            else:
#              projection_result_x = -y_tilde[0][0] + grad_f[0][0]*y_tilde[0][0]*f_x
#
#          if f_y < 0:
#            projection_result_y = -y_tilde[1][0]
#          else:
#            if -grad_f[1][0] * y_tilde[1][0] < 0:
#              projection_result_y = -y_tilde[1][0]
#            else:
#              projection_result_y = -y_tilde[1][0] + grad_f[1][0]*y_tilde[1][0]*f_y
#
#          if f_z < 0:
#            projection_result_z = -y_tilde[2][0]
#          else:
#            if -grad_f[2][0] * y_tilde[2][0] < 0:
#              projection_result_z = -y_tilde[2][0]
#            else:
#              projection_result_z = -y_tilde[2][0] + grad_f[2][0]*y_tilde[2][0]*f_z
#        
#          ## multiply by adaptive Gain and integrate 
#          #sigma = self.sigma_hat[0][0] + dt*(self.Gamma*projection_result)
#          
#          sigma_x = self.clamp( self.sigma_hat[0][0] + dt*(self.Gamma*projection_result_x) , self.sigma_hat_max*(1+self.epsilon_sigma) )
#          sigma_y = self.clamp( self.sigma_hat[1][0] + dt*(self.Gamma*projection_result_y) , self.sigma_hat_max*(1+self.epsilon_sigma) )
#          sigma_z = self.clamp( self.sigma_hat[2][0] + dt*(self.Gamma*projection_result_z) , self.sigma_hat_max*(1+self.epsilon_sigma) )
#          self.sigma_hat = np.array([[sigma_x],[sigma_y],[sigma_z]])
#          #self.sigma_hat = self.clamp(self.sigma_hat + dt*(-self.Gamma*y_tilde), self.sigma_hat_max)
#          #if not np.isnan(self.sigma_hat[0][0]):
#          #  print 'sigma_hat', '\n', self.sigma_hat, '\n'

                  
          # find desired velocity for L1 output from proportional controller
          self.desired_vel = self.Pgain*( np.reshape(des.x, (3,-1)) - np.reshape(curr.x, (3,-1)) )

        
          ### Find revised x_dot desired by low-pass filtering tracking error ###
          #track_error = np.reshape(des.x_dot, (3,-1)) - self.sigma_hat
          track_error = self.desired_vel - self.sigma_hat
        
          if self.LPF_type == 1:
            ###	First Order Low Pass Filter
            # low pass filter C(s) = omega_cutoff / (s + omega_cutoff) ### NOTE: decoupled directions
            self.x_L1_des = self.x_L1_des + dt*self.omega_cutoff.dot( -self.x_L1_des + track_error )

          elif self.LPF_type == 3:
            #### Third Order Low Pass Filter y = C(s)*u
            self.u_dot[0][0] = 1/dt*(track_error[0][0] - self.u[0][0]) # u_dot = 1/dt*(u - u_old)
            self.u_dot[1][0] = 1/dt*(track_error[1][0] - self.u[1][0]) # u_dot = 1/dt*(u - u_old)
            self.u_dot[2][0] = 1/dt*(track_error[2][0] - self.u[2][0]) # u_dot = 1/dt*(u - u_old)

            self.u = track_error # set current u to track_error (in next iteration, this is automatically u_old)
      
            self.y_ddot[0][0] = self.y_ddot[0][0] + dt*(-3*self.omega_cutoff[0][0]*(self.y_ddot[0][0]) - 3*(self.omega_cutoff[0][0]**2)*(self.y_dot[0][0]) - (self.omega_cutoff[0][0]**3)*(self.y[0][0]) + 3*(self.omega_cutoff[0][0]**2)*(self.u_dot[0][0]) + (self.omega_cutoff[0][0]**3)*(self.u[0][0]) )

            self.y_ddot[1][0] = self.y_ddot[1][0] + dt*(-3*self.omega_cutoff[1][1]*(self.y_ddot[1][0]) - 3*(self.omega_cutoff[1][1]**2)*(self.y_dot[1][0]) - (self.omega_cutoff[1][1]**3)*(self.y[1][0]) + 3*(self.omega_cutoff[1][1]**2)*(self.u_dot[1][0]) + (self.omega_cutoff[1][1]**3)*(self.u[1][0]) )

            self.y_ddot[2][0] = self.y_ddot[2][0] + dt*(-3*self.omega_cutoff[2][2]*(self.y_ddot[2][0]) - 3*(self.omega_cutoff[2][2]**2)*(self.y_dot[2][0]) - (self.omega_cutoff[2][2]**3)*(self.y[2][0]) + 3*(self.omega_cutoff[2][2]**2)*(self.u_dot[2][0]) + (self.omega_cutoff[2][2]**3)*(self.u[2][0]) )


            self.y_dot[0][0] = self.y_dot[0][0] + dt*(self.y_ddot[0][0])
            self.y_dot[1][0] = self.y_dot[1][0] + dt*(self.y_ddot[1][0])
            self.y_dot[2][0] = self.y_dot[2][0] + dt*(self.y_ddot[2][0])

            self.y[0][0] = self.y[0][0] + dt*(self.y_dot[0][0])
            self.y[1][0] = self.y[1][0] + dt*(self.y_dot[1][0])
            self.y[2][0] = self.y[2][0] + dt*(self.y_dot[2][0])
        
####	Third Order Low Pass Filter y = C(s)*u
##          # low pass filter C(s) = (3*omega_cutoff^2*s + omega_cutoff^3)/(s^3 + 3*omega_cutoff*s^2 + 3*omega_cutoff^2*s + omega_cutoff^3)
##          
##          # first find derivative of input signal (i.e. u = track_error, u_dot = d/dt(track_error) )
#          self.u_dot = 1/dt*(track_error - self.u) # u_dot = 1/dt*(u - u_old)
#          self.u = track_error # set current u to track_error (in next iteration, this is automatically u_old)
#        
#          self.y_ddot = self.y_ddot + dt*(-3*self.omega_cutoff.dot(self.y_ddot) - 3*(self.omega_cutoff**2).dot(self.y_dot) - (self.omega_cutoff**3).dot(self.y) + 3*(self.omega_cutoff**2).dot(self.u_dot) + (self.omega_cutoff**3).dot(self.u) )
#          self.y_dot = self.y_dot + dt*(self.y_ddot)
#          self.y = self.y + dt*(self.y_dot)
        
            # low filter output is L1 desired velocity
            self.x_L1_des = self.y

          else:
            print "\n !!! NO FILTER TYPE !!! reverting to 1st order LPF"
            self.x_L1_des = self.x_L1_des + dt*self.omega_cutoff.dot( -self.x_L1_des + track_error )
          

          ### reference model -- M(s) = m/(s+m) -- x_ref = M(s)(u + sigma_hat) ###
          self.x_ref = self.x_ref + dt*self.B_m.dot( -self.x_ref + self.x_L1_des + self.sigma_hat )

        
          ### Log Data to csv file
          with open(self.save_dir + self.current_time + 'l1_ref_output.csv','ab') as ref_model:
            writer = csv.writer(ref_model)
            # time secs, time nsecs, x_ref(1:3), x_dot(1:3), sigma_hat(1:3), x_L1_des(1:3), x_dot_des(1:3), x(1:3), x_des(1:3), desired_acc(1:3), rpy(1:3)
            writer.writerow(np.array([now.secs, now.nsecs, self.x_ref[0][0], self.x_ref[1][0], self.x_ref[2][0], curr.x_dot[0], curr.x_dot[1], curr.x_dot[2], self.sigma_hat[0][0], self.sigma_hat[1][0], self.sigma_hat[2][0], self.x_L1_des[0][0], self.x_L1_des[1][0], self.x_L1_des[2][0], self.desired_vel[0][0], self.desired_vel[1][0], self.desired_vel[2][0], curr.x[0], curr.x[1], curr.x[2], des.x[0], des.x[1], des.x[2], 0,0,0, curr.rpy[0], curr.rpy[1], curr.rpy[2]]))
      
        ###########################################################################

        # Z-velocity command m/sec)
        z_velocity_out = self.x_L1_des[2][0]
        
        # calculate the desired acceleration in x and y (global coordinates, [m/s^2] )
        ax = (2.0*self.zeta/self.tau_x)*(self.x_L1_des[0][0] - curr.x_dot[0]) + (1.0/(self.tau_x*self.tau_x))*(des.x[0]-curr.x[0]) ### NOTE: x_L1_des x-position
        ay = (2.0*self.zeta/self.tau_x)*(self.x_L1_des[1][0] - curr.x_dot[1]) + (1.0/(self.tau_x*self.tau_x))*(des.x[0]-curr.x[1]) ### NOTE: x_L1_des y-position
        
      else:
        # use standard controller
        
        # Z-velocity command m/sec)
        z_velocity_out = (1.0/(self.tau_z**2))*( des.x[2] - curr.x[2] )
        #z_velocity_out =  ((2.0*self.zeta/self.tau_z) * (des.x_dot[2] - curr.x_dot[2]) + (1.0/(self.tau_z**2))*(des.x[2] - curr.x[2]) )
    
        # calculate the desired acceleration in x and y (global coordinates, [m/s^2] )
        ax = (2.0*self.zeta/self.tau_x)*(des.x_dot[0] - curr.x_dot[0]) + (1.0/(self.tau_x*self.tau_x))*(des.x[0]-curr.x[0])
        ay = (2.0*self.zeta/self.tau_x)*(des.x_dot[1] - curr.x_dot[1]) + (1.0/(self.tau_x*self.tau_x))*(des.x[1]-curr.x[1])
      
        # append to csv file
        with open(self.save_dir + self.current_time + 'std_ctrl_output.csv','ab') as ref_model:
          writer = csv.writer(ref_model)
          #time secs, time nsecs, x_ref(1:3), x_dot(1:3), sigma_hat(1:3), x_L1_des(1:3), x_dot_des(1:3), x(1:3), x_des(1:3)
          writer.writerow(np.array([now.secs, now.nsecs, 0,0,0, curr.x_dot[0], curr.x_dot[1], curr.x_dot[2], 0,0,0, 0,0,0, 0,0,0, curr.x[0], curr.x[1], curr.x[2], des.x[0], des.x[1], des.x[2], 0,0,0, curr.rpy[0], curr.rpy[1], curr.rpy[2]]))
  
      
      self.pubXcurr_x.publish(curr.x[0])
      self.pubXcurr_y.publish(curr.x[1])
      self.pubXcurr_z.publish(curr.x[2])

      self.pubXdotcurr_x.publish(curr.x_dot[0])
      self.pubXdotcurr_y.publish(curr.x_dot[1])
      self.pubXdotcurr_z.publish(curr.x_dot[2])

      # Yaw rate command (rad/sec)??
      yaw_err = np.mod(des.rpy[2]-curr.rpy[2] + np.pi, 2.*np.pi) - np.pi
      yaw_velocity_out = (1.0 / self.tau_w) * yaw_err
  
      # Roll/Pitch Commands
      # determine the mass-normalized thrust
      thrust = np.linalg.norm(np.array([0.,0.,self.g]) + curr.x_ddot)

      # keep ax,ay < thrust (so that arcsin is defined)
      if thrust == 0.0:
        print "########## thrust is zero ##########"
        ax_clamped = 1.0
        ay_clamped = 1.0
      else:
        ax_clamped = self.clamp(ax / thrust, 1.0)
        ay_clamped = self.clamp(ay / thrust, 1.0)
   
      # Rotate desired accelerations into drone's body frame
      ax_b =  ax_clamped*np.cos(curr.rpy[2]) + ay_clamped*np.sin(curr.rpy[2])
      ay_b = -ax_clamped*np.sin(curr.rpy[2]) + ay_clamped*np.cos(curr.rpy[2])
  
      ax_b = self.clamp(ax_b, 0.95)
      ay_b = self.clamp(ay_b, 0.95)

      # convert acceleration into roll/pitch angles [rad]
      pitch_out =  np.arcsin(ax_b)
      roll_out  = -np.arcsin(ay_b)
  
      ### Check for valid outputs and LIMIT if necessary
      if np.fabs(pitch_out) > 0.75:
        print "pitch: ", pitch_out, "   ax: ", ax, "   ay: ", ay, "   dt: ", dt
        print "pitch D term: ", check_pitch_D_term
        pitch_out = np.sign(pitch_out)*0.75

      elif np.isnan(pitch_out):
        print "pitch is NaN before sendCommand -- ax_b: ", ax_b
        pitch_out = 0.0
      
      if np.fabs(roll_out) > 0.75:
        print "roll: ", roll_out, "   ax: ", ax, "   ay: ", ay, "   dt: ", dt
        print "roll D term: ", check_roll_D_term
        roll_out = np.sign(roll_out)*0.75

      elif np.isnan(roll_out):
        print "roll is NaN before sendCommand -- ay_b: ", ay_b
        roll_out = 0.0
  
#############
# DEBUGGING #
#############
      self.command.twist.angular.x = (des.x[0]-curr.x[0]);
      self.command.twist.angular.y = (des.x_dot[0]-curr.x_dot[0]);
  
  
      # send the commands to the drone if the keyboard is not currently being used
      if(self.status.keyboard_override == 0):
        self.status.t_last_cmd = time.time()
  
        if math.isnan(pitch_out):
          print "pitch is NaN before sendCommand"
  
        if math.isnan(roll_out):
          print "roll is NaN before sendCommand"
  
        if self.angles_log:
          with open(self.save_dir + self.current_time + 'l1_angles.csv','ab') as angles:
            writer = csv.writer(angles)
            writer.writerow(np.array([roll_out, pitch_out, yaw_velocity_out, z_velocity_out, curr.rpy[0], curr.rpy[1], curr.x_dot[2], now.secs, now.nsecs]))
        
        self.SendCommand(roll_out, pitch_out, yaw_velocity_out, z_velocity_out)

  #****************************************************************************
 
  # Publish Commands to the drone if we are not in hover mode.
  def SendCommand(self, roll, pitch, yaw_rate, z_dot):
    
    # add artificial pitch to perturb system
    if self.change_pitch:
      pitch = pitch*self.change_output_factor
      roll = roll*self.change_output_factor
      z_dot = z_dot*self.change_output_factor


    if not(math.isnan(pitch)):
      self.command.twist.linear.x = pitch
    else:
      print "**********************pitch is NaN****************************\n"
      self.command.twist.linear.x = 0

    if not(math.isnan(roll)):
      self.command.twist.linear.y = roll
    else:
      print "**********************roll is NaN**********************\n"
      self.command.twist.linear.y = 0

    if not(math.isnan(z_dot)):
      self.command.twist.linear.z = z_dot
    else:
      print "**********************z_dot is NaN**********************\n"
      self.command.twist.linear.z = 0

    if not(math.isnan(yaw_rate)):
      self.command.twist.angular.z = yaw_rate
    else:
      print "**********************yaw_rate is NaN**********************\n"
      self.command.twist.angular.z = 0

    # make sure the drone is not taking off
    if (self.status.drone_state != DroneStatus.TakingOff): 
      self.pubCommand.publish(self.command.twist)

    


  #***************    Callback Functions for Measurements   *******************

  def updateNavdata(self,nav_msg):
    self.status.drone_state = nav_msg.state
    
    self.navdata_rpy = np.array([[nav_msg.rotX*np.pi/180.0],[nav_msg.rotY*np.pi/180.0],[nav_msg.rotZ*np.pi/180.0]])

  #****************************************************************************
  # This method updates the current state of the drone
  def updateCurrentState(self,curr_data):

    # update the state information
    self.current_state.x      = curr_data.pos
    self.current_state.x_dot  = curr_data.vel
    self.current_state.x_ddot = curr_data.acc
    self.current_state.rpy    = curr_data.euler

    # Determine the commands to be sent to the drone
    self.determineCommands()

  #****************************************************************************

  # Update the desired state 
  def updateDesiredState(self,desiredState):

    # Update the desired state information
    self.desired_state.x      = np.array([desiredState.x,  desiredState.y,  desiredState.z],  dtype=np.float64)
    self.desired_state.x_dot  = np.array([desiredState.vx, desiredState.vy, desiredState.vz], dtype=np.float64)
    self.desired_state.x_ddot = np.array([desiredState.ax, desiredState.ay, desiredState.az], dtype=np.float64)
    self.desired_state.rpy    = np.array([desiredState.roll, desiredState.pitch, desiredState.yaw], dtype=np.float64)

  #****************************************************************************

  def clamp(self, num, upper=1.0, lower=None):
    if (lower is None):
      num = max(min(num,upper),-1.0*upper)
    else:
      num = max(min(num,upper),lower)
    return (num)

  #****************************************************************************

  # Sends a land signal to all flying drones
  def SendLandAll(self):
    self.pubLandAll.publish(Empty())
  
  # Sends a takeoff signal to all flying drones
  def SendTakeoffAll(self):
    self.pubTakeoffAll.publish(Empty())

  # Sends a savetofile signal to save d and u variables
  def SendSaveToFile(self):
    self.pubSaveToFile.publish(Empty())

  # Sends a readfromfile signal to read file with d and u variables
  def SendReadFromFile(self):
    self.pubReadFromFile.publish(Empty())

  # Send an emergency (or reset) message to the ardrone driver
  def SendEmergency(self):
    self.pubReset.publish(Empty())

  # Send a takeoff message to the ardrone driver
  def SendTakeoff(self,called=None):
    self.pubTakeoff.publish(Empty())
  
  # Send a landing message to the ardrone driver
  def SendLand(self,called=None):
    self.pubLand.publish(Empty())


  # Send Empty messages on /GoInitPos and /StartExp topics
  def sendGoInitPos(self):
    self.pubGoInitPos.publish(Empty())
    print "GoingToInitPos!"

  def sendStartExp(self):
    self.azhat = 0
    self.azhatdot = 0
    self.bzhat = 0 
    self.bzhatdot = 0
    self.pubStartExp.publish(Empty())
    #print "StartingExperiment!"

  #****************************************************************************
  # Keyboard Controller
  #****************************************************************************

  # This method is called when a key is pressed. It overrides the automated commands.
  def keyPressEvent(self, event):
    key = event.key()

    # If the key is not generated from an auto-repeating key
    if (not event.isAutoRepeat()):
      # Turn on override
      self.status.keyboard_override = 1 # turn on override
      roll_out = 0.0
      pitch_out = 0.0
      yaw_velocity_out = 0.0
      z_velocity_out = 0.0
      print "Key received"
      # Handle the important cases first!
      if key == KeyMapping.LandAll:
        self.SendLandAll()
      elif key == KeyMapping.TakeoffAll:
        self.SendTakeoffAll()
      elif key == KeyMapping.Emergency:
        self.SendEmergency()
      elif key == KeyMapping.Takeoff:
        self.start_flight_timer = True
        self.start_time = rospy.get_rostime().secs
        print "started L1 countdown"
        self.SendTakeoff()
      elif key == KeyMapping.Land:
        self.SendLand()
      elif key == KeyMapping.StartHover:
        self.hover = 1
      elif key == KeyMapping.EndHover:
        self.hover = 0
      elif key == KeyMapping.SaveToFile:
        self.SendSaveToFile()
      elif key == KeyMapping.ReadFromFile:
        self.SendReadFromFile()
      elif key == KeyMapping.GoInitPos: # send empty messages on /function1 topic 
        self.sendGoInitPos() 
      elif key == KeyMapping.StartExp: # send empty messages on /function2 topic 
        print "Starting experiment :)-" 
        self.sendStartExp()
      elif key == KeyMapping.ChangePitchOut:
        print "Changed roll-pitch-zdot output by a factor of ", self.change_output_factor
        self.change_pitch = True
      else:
        # Now we handle moving, notice that this section is the opposite (+=) of the keyrelease section
        if key == KeyMapping.YawLeft:
          yaw_velocity_out = self.max_yaw
        elif key == KeyMapping.YawRight:
          yaw_velocity_out = -self.max_yaw
        elif key == KeyMapping.ForwardLeft:
          pitch_out = self.max_euler
          roll_out = self.max_euler
        elif key == KeyMapping.Forward:
          pitch_out = self.max_euler
        elif key == KeyMapping.ForwardRight:
          pitch_out = self.max_euler
          roll_out = -self.max_euler
        elif key == KeyMapping.Right:
          roll_out = -self.max_euler
        elif key == KeyMapping.BackwardRight:
          pitch_out = -self.max_euler
          roll_out = -self.max_euler
        elif key == KeyMapping.Backward:
          pitch_out = -self.max_euler
        elif key == KeyMapping.BackwardLeft:
          pitch_out = -self.max_euler
          roll_out = self.max_euler
        elif key == KeyMapping.Left:
          roll_out = self.max_euler
        elif key == KeyMapping.IncreaseAltitude:
          z_velocity_out = self.max_vz
        elif key == KeyMapping.DecreaseAltitude:
          z_velocity_out = -self.max_vz
      
      self.SendCommand(roll_out, pitch_out, yaw_velocity_out, z_velocity_out)

  #****************************************************************************

  def keyReleaseEvent(self,event):
    key = event.key()

    # If the key is not generated from an auto-repeating key
    if (not event.isAutoRepeat()):
      # Note that we don't handle the release of emergency/takeoff/landing keys here, there is no need.
      self.status.keyboard_override = 0 # turn off override
      self.SendCommand(0, 0, 0, 0)

#####################     Main Code to Run      ################################
if __name__=='__main__':

  # set up the signal handeler 
  signal.signal(signal.SIGINT, sigint_handler)

  # First we setup a ros node, so that we can communicate with the other packages
  rospy.init_node('nonlinear_controller')
  
  # Now we construct our Qt Application and associated windows
  app = QtGui.QApplication(sys.argv)
  display = DroneController()
  display.show()
  # executes the QT application
  status = app.exec_()

  # and only progresses to here once the application has been shutdown
  rospy.signal_shutdown('Great Flying!')
  sys.exit(status)
